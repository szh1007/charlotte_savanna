"""把多份评测报告拼成对照组 (C17).

```bash
python -m app.rag_eval.compare artifacts/eval_arm_*.json
```

**为什么要专门一个工具**: 一份报告只讲一趟的数字. 「精排开/关哪个好」是个对照问题,
要的是同一批题, 同一套参数下**两臂**的逐层指标并排, 外加**臂内极差** —— 单趟跑出来
的差不能当结论 (机器负载, 模型服务抖动都在里面), 只有臂内极差明显小于两臂之差,
那个差才归因得到精排头上.

分组靠报告里的 `运行配置.臂`, 不靠文件名 —— 文件名是人起的, 会写错.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

# 报告里固定的两个逐层指标, 以及只在最终层有的精确率
FIXED_METRICS = ("平均召回率", "平均必命中率")
PRECISION_METRIC = "平均精确率"
# MRR/NDCG 的 K 跟着 `RERANK_MAX_TOPK` 走, 键名会变 (`平均MRR@8` -> `平均MRR@1`).
# **不能写死**: 写死之后报告键一改, 这里取不到值就静默少报两项, 而且不报错 ——
# 对照表少了指标, 读的人只会以为"就这几项".
RANK_METRIC_PREFIXES = ("平均MRR@", "平均NDCG@")


def _rank_metric_keys(reports: list[dict[str, Any]]) -> list[str]:
    """从报告里发现 MRR/NDCG 的实际键名 (跟着当前 K 走)."""
    sample = reports[0]["汇总结果"]["分层汇总"]
    layer = next(iter(sample.values()), {})
    keys = sorted(k for k in layer if k.startswith(RANK_METRIC_PREFIXES))
    if not keys:
        raise ValueError(
            "报告里找不到 MRR/NDCG 指标键 —— 先确认报告是自己生成的 "
            "(键名形如 平均MRR@<K>)"
        )
    return keys


def load_reports(paths: list[Path]) -> list[dict[str, Any]]:
    """读报告; 顺手记下来源文件名, 便于报告里回溯是哪几趟."""
    reports = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data["_来源"] = Path(path).name
        reports.append(data)
    return reports


def group_by_arm(reports: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按 `运行配置.臂` 分组; 没有臂名的旧报告归到「未标注」."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        arm = (report.get("运行配置") or {}).get("臂", "未标注")
        groups.setdefault(arm, []).append(report)
    return groups


def _spread(values: list[float]) -> float | None:
    """臂内极差; 单趟时返回 None —— 不能拿 0 冒充"稳定"."""
    if len(values) < 2:
        return None
    return round(max(values) - min(values), 4)


def summarize_arm(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """一个臂的汇总: 逐层指标的均值 + 臂内极差 + 逐趟明细."""
    metrics = (*FIXED_METRICS, *_rank_metric_keys(reports))

    def metric_of(layer: str, metric: str) -> list[float]:
        out = []
        for report in reports:
            value = report["汇总结果"]["分层汇总"].get(layer, {}).get(metric)
            if value is not None:
                out.append(value)
        return out

    layers = list(reports[0]["汇总结果"]["分层汇总"].keys())
    layers_summary: dict[str, Any] = {}
    for layer in layers:
        entry: dict[str, Any] = {}
        for metric in metrics:
            values = metric_of(layer, metric)
            entry[metric] = {
                "均值": round(mean(values), 4) if values else None,
                "极差": _spread(values),
                "各趟": values,
            }
        if layer == layers[-1]:
            values = metric_of(layer, PRECISION_METRIC)
            if values:
                entry[PRECISION_METRIC] = {
                    "均值": round(mean(values), 4),
                    "极差": _spread(values),
                    "各趟": values,
                }
        layers_summary[layer] = entry

    return {
        "趟数": len(reports),
        "来源": [r["_来源"] for r in reports],
        "题数": [r["汇总结果"].get("用例总数") for r in reports],
        "平均主体命中率": {
            "均值": round(mean([r["汇总结果"]["平均主体命中率"] for r in reports]), 4),
            "极差": _spread([r["汇总结果"]["平均主体命中率"] for r in reports]),
        },
        "分层": layers_summary,
    }


def compare(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """全部臂的汇总 + 两臂之差 (只有恰好两个臂时才算)."""
    groups = group_by_arm(reports)
    summary = {arm: summarize_arm(items) for arm, items in groups.items()}
    delta: dict[str, Any] = {}
    if len(summary) == 2:
        (arm_a, a), (arm_b, b) = list(summary.items())
        # 两臂的指标键都必须齐全 —— 少一项就说明有一边的报告是旧口径, 不能比
        metrics_a = set(a["分层"][next(iter(a["分层"]))])
        for layer in a["分层"]:
            layer_delta = {}
            for metric in sorted(metrics_a):
                va = a["分层"][layer].get(metric)
                vb = b["分层"][layer].get(metric)
                if va and vb and va["均值"] is not None and vb["均值"] is not None:
                    layer_delta[metric] = round(vb["均值"] - va["均值"], 4)
            if layer_delta:
                delta[f"{arm_b} - {arm_a} / {layer}"] = layer_delta
    return {"臂": summary, "两臂之差": delta}


def render_markdown(result: dict[str, Any]) -> str:
    """渲染成人看的表."""
    lines: list[str] = ["# rerank 开/关对照", ""]
    arms = result["臂"]
    for arm, data in arms.items():
        lines.append(f"## 臂: {arm}")
        lines.append("")
        lines.append(
            f"- 趟数: {data['趟数']} · 题数: {data['题数']} · 来源: {data['来源']}"
        )
        lines.append(
            f"- 平均主体命中率: {data['平均主体命中率']['均值']}"
            f"(极差 {data['平均主体命中率']['极差']})"
        )
        lines.append("")
        for layer, metrics in data["分层"].items():
            lines.append(f"### 层: {layer}")
            lines.append("")
            lines.append("| 指标 | 均值 | 臂内极差 | 各趟 |")
            lines.append("|---|---|---|---|")
            for metric, stat in metrics.items():
                lines.append(
                    f"| {metric} | {stat['均值']} | {stat['极差']} | {stat['各趟']} |"
                )
            lines.append("")
    if result["两臂之差"]:
        lines.append("## 两臂之差")
        lines.append("")
        lines.append("| 层 (后减前) | 指标 | 差 |")
        lines.append("|---|---|---|")
        for key, metrics in result["两臂之差"].items():
            for metric, value in metrics.items():
                lines.append(f"| {key} | {metric} | {value:+.4f} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="评测报告对照 (C17)")
    parser.add_argument("reports", nargs="+", type=Path, help="两份及以上的报告 json")
    parser.add_argument("--out", type=Path, default=None, help="对照结果落盘目录")
    args = parser.parse_args()

    reports = load_reports(args.reports)
    result = compare(reports)
    markdown = render_markdown(result)
    print(markdown)

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "compare.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (args.out / "compare.md").write_text(markdown + "\n", encoding="utf-8")
        print(f"\n落盘: {args.out}/compare.json · compare.md")


if __name__ == "__main__":
    main()
