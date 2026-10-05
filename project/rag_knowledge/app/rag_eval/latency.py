"""把日志里的逐节点耗时读回来, 汇总成 P50 / P95 (C18).

为什么是"读日志"而不是改评测包:
- `@node_log` 已经给每个节点打了 `耗时=Nms`, 数据本来就在, 缺的只是汇总;
- 评测报告是**指标**报告 (召回率 / 精确率), 它的口径只跟题库有关; 耗时还跟
  机器当前在忙什么有关 —— 混在一起会让报告每跑一次就变一次, 且分不清是
  检索变差了还是机器变忙了. 耗时单独落一份, 谁需要谁读.

口径 (读数字之前先看这里):
- 一个 `追踪 ID` = 一次问答; 同名的节点在一次问答里只会出现一次 (评测跑批器
  是逐节点顺序调的), 取最后一条即可.
- 评测跑批器里 `_09_1 普通检索` 与 `_09_2 HyDE 检索` 是**串行调用**的, 而线上
  查询图里它们是**并行**的两条边. 所以这里的"单次合计"是**评测口径**, 比线上
  墙钟偏大 —— 它是上界, 别当成用户等待时间.
- 评测链路不含 `_09_3 联网` 与 `_12 作答生成` (前者不参与评测, 后者是流式的
  且要另算 TTFT), 两者都不在这张表里.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

# 日志行: `... | INFO | file:func:line - [node_xxx] 节点完成, 追踪 ID=yyy, 耗时=123ms`
# 节点名与追踪 ID 都从**消息体**里取 —— 前缀那截会被 fix_log_position 改写成
# 业务文件位置, 不是可依赖的字段.
NODE_DONE = re.compile(
    r"\[(?P<node>node_[A-Za-z0-9_]+)\]\s*节点完成, 追踪 ID=(?P<trace>[^,]+), "
    r"耗时=(?P<ms>\d+)ms"
)


def percentile(values: list[float], pct: float) -> float:
    """最近秩法 (nearest-rank) 的百分位 —— 不插值.

    样本量是"40 次问答"这个量级: 插值只会造出一个没人真跑出来过的数,
    最近秩法给的必定是某个真实样本. 秩取 `ceil(pct/100 * n)` (1 基),
    偶数个样本时 P50 因此落在**靠前**那一个上.
    """
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = math.ceil(pct / 100 * len(ordered)) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]


def parse_node_durations(lines: Iterable[str]) -> dict[str, dict[str, int]]:
    """日志行 → `{追踪 ID: {节点名: 毫秒}}`."""
    per_trace: dict[str, dict[str, int]] = defaultdict(dict)
    for line in lines:
        match = NODE_DONE.search(line)
        if match is None:
            continue
        per_trace[match["trace"].strip()][match["node"]] = int(match["ms"])
    return dict(per_trace)


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
    return {
        "n": len(values),
        "p50_ms": round(percentile(values, 50), 1),
        "p95_ms": round(percentile(values, 95), 1),
        "min_ms": round(min(values), 1),
        "max_ms": round(max(values), 1),
        "mean_ms": round(sum(values) / len(values), 1),
    }


def build_report(per_trace: dict[str, dict[str, int]], log_path: str) -> dict:
    """逐节点 + 每次问答合计的汇总."""
    node_values: dict[str, list[float]] = defaultdict(list)
    totals: list[float] = []
    for durations in per_trace.values():
        if not durations:
            continue
        for node, ms in durations.items():
            node_values[node].append(float(ms))
        totals.append(float(sum(durations.values())))

    return {
        "log": log_path,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "queries": len(totals),
        "nodes": {
            node: summarize(values) for node, values in sorted(node_values.items())
        },
        "per_query_total": summarize(totals),
        "notes": [
            "耗时来自日志里的 @node_log, 逐节点; 不含联网召回与作答生成.",
            "评测跑批器里普通检索与 HyDE 检索是串行调的, 线上查询图里是并行 — "
            "所以 per_query_total 是评测口径的上界, 不是用户看到的等待时间.",
        ],
    }


def render_markdown(report: dict) -> str:
    rows = [
        "| 节点 | n | P50 (ms) | P95 (ms) | 均值 (ms) |",
        "|---|---|---|---|---|",
    ]
    for node, stat in report["nodes"].items():
        rows.append(
            f"| `{node}` | {stat['n']} | {stat['p50_ms']:.0f} | "
            f"{stat['p95_ms']:.0f} | {stat['mean_ms']:.0f} |"
        )
    total = report["per_query_total"]
    rows.append(
        f"| **单次问答合计 (评测口径)** | {total['n']} | {total['p50_ms']:.0f} | "
        f"{total['p95_ms']:.0f} | {total['mean_ms']:.0f} |"
    )
    return "\n".join(rows)


def _latest_log() -> Path:
    log_dir = Path(__file__).resolve().parents[2] / "logs"
    logs = sorted(log_dir.glob("app_*.log"))
    if not logs:
        raise SystemExit(f"没有日志文件: {log_dir}")
    return logs[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description="逐节点耗时汇总 (C18)")
    parser.add_argument("--log", type=Path, default=None, help="默认取最新的日志")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "artifacts",
        help="报告落盘目录",
    )
    args = parser.parse_args()

    log_path = args.log or _latest_log()
    per_trace = parse_node_durations(
        log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    )
    report = build_report(per_trace, str(log_path))

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.out / f"latency_{stamp}.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print(f"日志: {log_path} (问答 {report['queries']} 次)")
    print(render_markdown(report))
    print(f"\n报告落盘: {out_path}")


if __name__ == "__main__":
    main()
