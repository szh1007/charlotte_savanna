"""三路召回节点的延迟测量 (C18).

用法::

    python -m app.eval.latency --capture          # 抓一份冻结的关键词扩展结果
    python -m app.eval.latency --mode recall      # 重放, 量"嵌入 + 检索"这一段
    python -m app.eval.latency --mode node        # 重放, 量整个节点(含真 LLM 扩展)

**为什么要单开一个装置, 而不是拿跑批报告凑**: 跑批的 `duration_s` 是整条链的,
里面"生成 SQL"那次 LLM 调用抖动几秒到几十秒, 会把召回段的差整个淹掉 —— 而
C18 改的正是召回段. 两个口径都在这里量:

- `recall`: 关键词扩展用冻结结果替掉, 只剩"批量嵌入 + N 次检索". 前后两趟的
  输入完全一致, 差只可能来自代码改动 —— **A/B 用这个口径**.
- `node`: 扩展走真 LLM, 给的是线上会看到的口径. 它包含 LLM 的抖动, 所以
  只用于说明"省下的那点在一整个节点里占多大比例", 不用于 A/B.

**冻结为什么必要**: 关键词扩展是一次真 LLM 调用, 同一个问题两次跑给出的词表
不同; 词表不同, 后面的检索次数就不同, 两趟就不可比. `--capture` 把每个问题,
每个节点的扩展结果落盘 (`app/eval/fixtures/recall_keywords.json`), 之后重放
一律用它. jieba 那层本身就是确定性的, 不必冻.

前提: Qdrant / ES / 嵌入服务都在跑, 索引已由 `app.scripts.build_meta` 建好.
本文件不在 pytest 里 (要真服务), 与跑批器同属"手工跑的装置".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import RunnableLambda

from app.agent.nodes import _2_1_recall_column, _2_2_recall_metric, _2_3_recall_value
from app.agent.nodes._1_extract_keywords import extract_keywords
from app.clients.embedding import embedding_client
from app.clients.es import es_client
from app.clients.qdrant import qdrant_client
from app.core.log import logger
from app.eval.golden import GoldenCase, load_golden_set
from app.eval.report import REPORTS_DIR
from app.repositories.es.value import ValueEsRepository
from app.repositories.qdrant.column import ColumnQdrantRepository
from app.repositories.qdrant.metric import MetricQdrantRepository

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "recall_keywords.json"

# (名字, 节点函数, 节点所在模块) —— 模块是为了把它的 `llm` 换掉: 三个节点都是
# `from app.agent.llm import llm`, 换模块属性只影响这一个节点的链.
NODES = (
    ("recall_column", _2_1_recall_column.recall_column, _2_1_recall_column),
    ("recall_metric", _2_2_recall_metric.recall_metric, _2_2_recall_metric),
    ("recall_value", _2_3_recall_value.recall_value, _2_3_recall_value),
)

# 报告里带一句「这一档能不能拿来 A/B」: 落盘之后它就可能脱离 README 被单独读,
# 而两档的数字摆在一起长得一模一样, 读的人看不出 node 那一档**不该做对照**.
MODE_NOTES = {
    "recall": (
        "A/B 用这一档: 关键词扩展换成冻结结果, 前后两趟输入完全一致, "
        "只量『嵌入 + 检索』; 判优化有没有效看它的 P50."
    ),
    "node": (
        "线上口径: 含『关键词扩展』那次真 LLM 调用. 那一次调用自带秒级抖动、"
        "偶发几十秒 (60s 超时 + 2 次重试), 整节点的 P50/P95/极值都由它主导 —— "
        "**这一档不能用来做 A/B** (两趟输入不同、也没有交替跑), 只作量级参照. "
        "优化效果看 recall 档."
    ),
}


class _Runtime:
    """节点只用到 `context` 与 `stream_writer` 两个属性 —— 鸭子类型足够.

    (`langgraph.runtime.Runtime` 是具体类, 节点对它只做属性访问; 与
    `tests/doubles.FakeRuntime` 同一个理由, 那边是给用例用的, 不复用.)
    """

    def __init__(self, context: dict) -> None:
        self.context = context
        self.stream_writer = _ignore_event


def _ignore_event(event: dict) -> None:
    """接住节点推的 stage 事件 —— 这里是量耗时, 不要它们."""


def _build_context() -> dict:
    return {
        "embeddings": embedding_client.embeddings,
        "column_qdrant_repository": ColumnQdrantRepository(qdrant_client.client),
        "metric_qdrant_repository": MetricQdrantRepository(qdrant_client.client),
        "value_es_repository": ValueEsRepository(es_client.client),
    }


def _recording_llm(real_llm, sink: list[list[str]]):
    """把真 LLM 包一层: 结果照常返回给链, 同时留一份原始文本.

    必须仍然是个 `Runnable` —— 它在节点里是 `prompt | llm | parser` 中间那一环.
    """
    parser = JsonOutputParser()

    async def run(prompt_value):
        message = await real_llm.ainvoke(prompt_value)
        # 留下**解析后**的词表而不是原始文本: 重放时要喂回同一条链的下游 parser,
        # 形状对不上会在节点里炸成另一个错, 与"这次测量"无关
        sink.append(parser.parse(message.content))
        return message

    return RunnableLambda(run)


def _stub_llm(extension: list[str]) -> RunnableLambda:
    """重放时的假扩展: 不管问什么, 都回捕获时那一份词表.

    必须回 `AIMessage` 而不是裸 list —— 链的下一环是 `JsonOutputParser`,
    它要的是消息 (`Generation.text` 得是字符串), 喂 list 会炸在 pydantic 校验上.
    """
    payload = json.dumps(extension, ensure_ascii=False)
    return RunnableLambda(lambda _prompt_value: AIMessage(content=payload))


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------


async def _capture(cases: tuple[GoldenCase, ...], path: Path) -> dict:
    context = _build_context()
    runtime = _Runtime(context)
    captured: list[dict] = []

    for case in cases:
        # jieba 那一步是确定性的 (没有模型参与), 跟着一起存只是为了让重放的输入
        # 一眼可查 —— 真正的随机源是每个节点里的扩展调用
        keywords = (await extract_keywords({"query": case.question}, runtime))[
            "keywords"
        ]

        entry = {
            "id": case.id,
            "query": case.question,
            "keywords": keywords,
            "extensions": {},
        }
        for name, node, module in NODES:
            sink: list[list[str]] = []
            real_llm = module.llm
            module.llm = _recording_llm(real_llm, sink)
            try:
                await node({"query": case.question, "keywords": keywords}, runtime)
            finally:
                module.llm = real_llm
            entry["extensions"][name] = sink[-1]

        captured.append(entry)
        logger.info(f"[capture] {case.id} 关键词 {len(keywords)} 个")

    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "cases": captured,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def _load_fixture(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"没有冻结词表: {path}\n先跑 `python -m app.eval.latency --capture`"
        )
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


# ---------------------------------------------------------------------------
# 重放 + 计时
# ---------------------------------------------------------------------------


async def _replay(
    frozen: list[dict], mode: str, repeat: int, limit: int | None
) -> dict:
    cases = frozen[:limit] if limit else frozen
    context = _build_context()
    runtime = _Runtime(context)
    per_case: list[dict] = []

    for entry in cases:
        state = {"query": entry["query"], "keywords": entry["keywords"]}
        row: dict = {"id": entry["id"], "keywords": len(entry["keywords"])}

        for name, node, module in NODES:
            real_llm = module.llm
            if mode == "recall":
                module.llm = _stub_llm(entry["extensions"][name])
            durations: list[float] = []
            ids_per_repeat: list[list[str]] = []
            try:
                for _ in range(repeat):
                    started = time.perf_counter()
                    update = await node(state, runtime)
                    durations.append((time.perf_counter() - started) * 1000)
                    # 记 id 在计时**之后** —— 采集不占被测量的那段时间.
                    # 它是"这次改动有没有换掉召回结果"的唯一证据: 耗时变快但
                    # 结果集变了, 那是改坏了而不是优化.
                    ids_per_repeat.append(_ids_of(name, update))
            finally:
                module.llm = real_llm

            row[name] = durations
            row[f"{name}__ids"] = ids_per_repeat
            logger.info(
                f"[replay:{mode}] {entry['id']} {name} "
                f"P50={_percentile(durations, 50):.0f}ms"
            )

        per_case.append(row)
        logger.info(f"[replay:{mode}] {entry['id']} 完成")

    return {
        "mode": mode,
        "repeat": repeat,
        "case_count": len(cases),
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        # 报告跟着自己走一句"这份能不能当 A/B 用" —— 落盘之后脱离了 README,
        # 下一次读它的人 (包括我自己) 只看得见数字, 看不出它对不对得上口径
        "notes": MODE_NOTES[mode],
        "nodes": {
            name: _summarize([d for row in per_case for d in row[name]])
            for name, _, _ in NODES
        },
        "keyword_counts": _summarize([float(row["keywords"]) for row in per_case]),
        "per_case": per_case,
    }


def _ids_of(node_name: str, update: dict) -> list[str]:
    """节点返回的召回 id 列表 —— 三个节点的 state 字段名各不相同.

    字段名对不上时**当场报错**, 不返回空表: 空表看起来像"这次没召回",
    会让整份报告静静地失去"结果有没有被换掉"这条证据.
    """
    field = {
        "recall_column": "retrieved_columns",
        "recall_metric": "retrieved_metrics",
        "recall_value": "retrieved_values",
    }[node_name]
    if field not in update:
        raise KeyError(f"{node_name} 没返回 {field}, 该字段是不是改名了?")
    return [str(item["id"]) for item in update[field] or []]


def _percentile(values: list[float], pct: float) -> float:
    """最近秩法 (nearest-rank) 的百分位 —— 不插值.

    `statistics.quantiles` 是插值且 n=1 时会抛; 这里的样本量小 (几十条),
    插值只会造出一个"没人真跑出来过"的数, 最近秩法给的必定是某个真实样本.
    """
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = math.ceil(pct / 100 * len(ordered)) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]


def _summarize(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50_ms": round(_percentile(values, 50), 1),
        "p95_ms": round(_percentile(values, 95), 1),
        "min_ms": round(min(values), 1) if values else 0.0,
        "max_ms": round(max(values), 1) if values else 0.0,
        "mean_ms": round(sum(values) / len(values), 1) if values else 0.0,
    }


def _print_table(report: dict) -> None:
    print(
        f"\nmode={report['mode']} repeat={report['repeat']} "
        f"cases={report['case_count']}"
    )
    print(f"{'node':<16}{'n':>5}{'P50(ms)':>10}{'P95(ms)':>10}{'mean(ms)':>10}")
    for name, stat in report["nodes"].items():
        print(
            f"{name:<16}{stat['n']:>5}{stat['p50_ms']:>10.1f}"
            f"{stat['p95_ms']:>10.1f}{stat['mean_ms']:>10.1f}"
        )
    counts = report["keyword_counts"]
    print(
        f"关键词个数: min={counts['min_ms']:.0f} "
        f"P50={counts['p50_ms']:.0f} max={counts['max_ms']:.0f}"
    )


def _write_report(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"latency_{report['mode']}_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


async def _run(args: argparse.Namespace) -> None:
    embedding_client.init()
    qdrant_client.init()
    es_client.init()
    try:
        if args.capture:
            cases = load_golden_set()
            selected = cases[: args.limit] if args.limit else cases
            payload = await _capture(selected, args.fixture)
            print(f"已冻结 {len(payload['cases'])} 条到 {args.fixture}")
            return

        report = await _replay(
            _load_fixture(args.fixture), args.mode, args.repeat, args.limit
        )
        _print_table(report)
        print(f"\n报告落盘: {_write_report(report, args.out)}")
        if args.mode == "node":
            # 提醒一句: 这份文件**别入库** —— 它含 LLM 抖动, 摆进 reports/ 会误导
            # 下一个读它的人 (README §3.4 记着这次教训)
            print("(node 档报告不入库, 也不要拿来做前后对照 —— 见 README §3.4)")
    finally:
        await qdrant_client.close()
        await es_client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="三路召回节点的延迟测量 (C18)")
    parser.add_argument("--capture", action="store_true", help="抓冻结词表")
    parser.add_argument(
        "--mode",
        choices=("recall", "node"),
        default="recall",
        help="recall=只量嵌入+检索(A/B 用); node=整个节点(含真 LLM)",
    )
    parser.add_argument("--repeat", type=int, default=5, help="每条问题跑几次")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
