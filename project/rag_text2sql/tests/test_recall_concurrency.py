"""三路召回节点内的并发化 (C18).

改之前每个节点是**逐关键词串行**: `for keyword in merged_keywords:` 里
`await embeddings.aembed_query(keyword)` 再 `await repo.search(...)`, N 个关键词
就是 2N 个 RTT 相加 —— 实测 (2026-10-06) 列召回 P50 1420ms, 其中绝大多数是排队.

改之后是「一次批量嵌入 + 并发检索」. 这里钉住四件事:

1. 嵌入走**批量接口**: 一次调用带全部关键词 (`aembed_query` 不再被调用)
2. 检索**并发**, 且并发度**不超过**配置的上限 —— 上限是给嵌入服务 / Qdrant /
   ES 的连接池留的余量, 不是装饰
3. 上限**可配** (改 `app_config.recall.concurrency` 立即生效)
4. 结果的**顺序与去重语义不变** —— 顺序变了, 同一个字段被多个关键词召回时的
   取舍就跟着变, 那是悄悄改结果

第 4 条最要紧: 并发化最容易踩的坑不是慢, 是**结果悄悄换了**.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from doubles import FakeRuntime, qdrant_column
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.nodes import _2_1_recall_column, _2_2_recall_metric, _2_3_recall_value
from app.conf.app_config import app_config
from app.core.concurrency import gather_limited

COLUMN_REPO = "column_qdrant_repository"
METRIC_REPO = "metric_qdrant_repository"
VALUE_REPO = "value_es_repository"


def _vector_of(keyword: str) -> tuple[float, float]:
    """关键词 → 假向量 (与 `FakeEmbeddings` 的编码同款).

    有了它, 假仓储才能"按关键词"回放预置数据与延迟 —— 否则向量与关键词的
    对应关系只有节点自己知道, 用例就没法构造"先发的那条慢, 后发的那条快".
    """
    return (float(len(keyword)), float(ord(keyword[-1])))


class FakeEmbeddings:
    """记下每一次嵌入调用 —— 批量还是逐条, 从调用形状就能看出来."""

    def __init__(self) -> None:
        self.documents_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.documents_calls.append(list(texts))
        return [list(_vector_of(text)) for text in texts]

    async def aembed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [0.0]


class FakeVectorRepository:
    """Qdrant 那一类仓储的替身: 记录并发峰值, 按向量回放预置 payload."""

    def __init__(
        self,
        delay: float | dict[tuple[float, float], float] = 0.02,
        payloads: dict[tuple[float, float], list[dict]] | None = None,
    ) -> None:
        self.delay = delay
        self.payloads = payloads or {}
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    async def search(self, embedding: list[float]) -> list:
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            key = tuple(embedding)
            delay = (
                self.delay.get(key, 0.02)
                if isinstance(self.delay, dict)
                else self.delay
            )
            await asyncio.sleep(delay)
            return self.payloads.get(key, [])
        finally:
            self.in_flight -= 1


class FakeValueRepository:
    """ES 那一类的替身: 入参是关键词而不是向量."""

    def __init__(
        self,
        delay: float | dict[str, float] = 0.02,
        hits: dict[str, list[dict]] | None = None,
    ) -> None:
        self.delay = delay
        self.hits = hits or {}
        self.in_flight = 0
        self.max_in_flight = 0
        self.searched: list[str] = []

    async def search(self, keyword: str) -> list:
        self.searched.append(keyword)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            delay = (
                self.delay.get(keyword, 0.02)
                if isinstance(self.delay, dict)
                else self.delay
            )
            await asyncio.sleep(delay)
            return self.hits.get(keyword, [])
        finally:
            self.in_flight -= 1


def _stub_extension(monkeypatch, module, extra: list[str]) -> None:
    """把节点里的「LLM 关键词扩展」换成固定输出.

    `prompt | llm | parser` 中间那一环必须是 Runnable, 所以用 RunnableLambda;
    回的又必须是 `AIMessage` 而不是裸 list —— 下一环 `JsonOutputParser` 要的是
    消息. 换模块属性, 只影响这一个节点.
    """
    payload = json.dumps(extra, ensure_ascii=False)
    stub = RunnableLambda(lambda _prompt: AIMessage(content=payload))
    monkeypatch.setattr(module, "llm", stub)


def _keywords(count: int) -> list[str]:
    return [f"关键词{i}" for i in range(count)]


# ---------------------------------------------------------------------------
# gather_limited 本身
# ---------------------------------------------------------------------------


async def test_gather_limited_keeps_input_order() -> None:
    """先发的不一定先回 —— 结果必须按入参顺序, 不能按完成顺序."""

    async def slow(value: int) -> int:
        await asyncio.sleep(0.02 if value == 0 else 0.001)
        return value

    assert await gather_limited([slow(i) for i in range(4)], limit=4) == [0, 1, 2, 3]


async def test_gather_limited_caps_in_flight() -> None:
    peak = 0
    in_flight = 0

    async def work() -> int:
        nonlocal peak, in_flight
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return 0

    await gather_limited([work() for _ in range(9)], limit=3)

    assert peak == 3, f"上限没生效, 峰值 {peak}"


async def test_gather_limited_treats_limit_below_one_as_serial() -> None:
    """配置写 0 或负数时按 1 处理 —— 否则 Semaphore(0) 会把整条链挂死."""
    assert await gather_limited([asyncio.sleep(0) for _ in range(3)], limit=0) == [
        None,
        None,
        None,
    ]


# ---------------------------------------------------------------------------
# 列召回 / 指标召回: 批量嵌入 + 并发检索
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "node", "repo_key"),
    [
        (_2_1_recall_column, _2_1_recall_column.recall_column, COLUMN_REPO),
        (_2_2_recall_metric, _2_2_recall_metric.recall_metric, METRIC_REPO),
    ],
)
async def test_keywords_go_through_one_batch_embedding(
    monkeypatch, module, node, repo_key
) -> None:
    """7 个关键词 = 1 次批量嵌入, 不是 7 次 `aembed_query`."""
    monkeypatch.setattr(app_config.recall, "concurrency", 4)
    embeddings = FakeEmbeddings()
    repo = FakeVectorRepository()
    _stub_extension(monkeypatch, module, ["扩展词"])
    runtime = FakeRuntime({"embeddings": embeddings, repo_key: repo})

    await node({"query": "上个月华北的销售额", "keywords": _keywords(7)}, runtime)

    assert len(embeddings.documents_calls) == 1, "应当只有一次批量嵌入"
    assert len(embeddings.documents_calls[0]) == 8, "7 个关键词 + 1 个扩展词"
    assert embeddings.query_calls == [], "逐条接口不该再被调用"


@pytest.mark.parametrize(
    ("module", "node", "repo_key"),
    [
        (_2_1_recall_column, _2_1_recall_column.recall_column, COLUMN_REPO),
        (_2_2_recall_metric, _2_2_recall_metric.recall_metric, METRIC_REPO),
    ],
)
async def test_searches_run_concurrently_bounded_by_config(
    monkeypatch, module, node, repo_key
) -> None:
    """并发度**恰好**是配置值: 8 个关键词, 上限 4 → 4 个在飞, 两批发完."""
    monkeypatch.setattr(app_config.recall, "concurrency", 4)
    repo = FakeVectorRepository(delay=0.03)
    _stub_extension(monkeypatch, module, [])
    runtime = FakeRuntime({"embeddings": FakeEmbeddings(), repo_key: repo})

    await node({"query": "上个月华北的销售额", "keywords": _keywords(8)}, runtime)

    assert repo.calls == 8
    assert repo.max_in_flight == 4


async def test_concurrency_limit_is_read_from_config(monkeypatch) -> None:
    """上限从 `app_config.recall.concurrency` 现取 —— 改配置立即生效."""
    monkeypatch.setattr(app_config.recall, "concurrency", 1)
    repo = FakeVectorRepository(delay=0.01)
    _stub_extension(monkeypatch, _2_1_recall_column, [])
    runtime = FakeRuntime({"embeddings": FakeEmbeddings(), COLUMN_REPO: repo})

    await _2_1_recall_column.recall_column(
        {"query": "上个月华北的销售额", "keywords": _keywords(5)}, runtime
    )

    assert repo.max_in_flight == 1, "上限配成 1 就该是串行"


async def test_merge_order_and_dedup_follow_keyword_order(monkeypatch) -> None:
    """**排在前面的那条慢**, 后发的那条快 —— 合并仍按关键词顺序, 不按谁先回来.

    并发化最容易犯的错是"谁先回来谁算数": 结果集看着差不多, 但同一个字段被
    多个关键词召回时, 留下的那一份换了 (描述 / 示例都可能不同).

    关键在于**把"慢"钉在排前面的那条上**: 节点遍历的是
    `list(set(keywords + 扩展词))`, 谁排在前由进程内哈希决定, 用例不预设 ——
    所以先取真实顺序 `merged`, 再按这个顺序给延迟. 否则"慢"可能落在后发的那条
    上, 一个"按完成顺序回填"的坏实现照样能过 (实测: 换个坏实现, 6 个哈希种子
    里有 2 个仍能通过 —— 这条用例原来就长这样, 规格轴评审抓出来的).
    """
    monkeypatch.setattr(app_config.recall, "concurrency", 4)
    keywords = ["关键词0", "关键词1"]
    # 与节点里的归一化同款 (扩展为空)
    merged = list(set(keywords))
    assert len(merged) == 2
    first, second = merged

    shared = qdrant_column("fact_order.order_amount", "order_amount", "fact_order")
    unique = qdrant_column("dim_region.province", "province", "dim_region")
    copies = {
        first: dict(shared, description=f"来自{first}"),
        second: dict(shared, description=f"来自{second}"),
    }
    repo = FakeVectorRepository(
        delay={_vector_of(first): 0.05, _vector_of(second): 0.005},
        payloads={
            _vector_of(first): [unique, copies[first]],
            _vector_of(second): [unique, copies[second]],
        },
    )
    _stub_extension(monkeypatch, _2_1_recall_column, [])
    runtime = FakeRuntime({"embeddings": FakeEmbeddings(), COLUMN_REPO: repo})

    update = await _2_1_recall_column.recall_column(
        {"query": "上个月华北的销售额", "keywords": keywords}, runtime
    )
    columns = update["retrieved_columns"]

    assert [c["id"] for c in columns] == [unique["id"], shared["id"]]
    assert columns[1]["description"] == f"来自{first}", "留下该是排在前面的那份"


# ---------------------------------------------------------------------------
# 取值召回: 只并发检索 (ES 那一路没有嵌入)
# ---------------------------------------------------------------------------


async def test_value_recall_searches_concurrently(monkeypatch) -> None:
    monkeypatch.setattr(app_config.recall, "concurrency", 3)
    repo = FakeValueRepository(delay=0.03)
    _stub_extension(monkeypatch, _2_3_recall_value, [])
    runtime = FakeRuntime({VALUE_REPO: repo})

    await _2_3_recall_value.recall_value(
        {"query": "上个月华北的销售额", "keywords": _keywords(6)}, runtime
    )

    assert repo.max_in_flight == 3
    assert len(repo.searched) == 6


async def test_value_recall_dedups_in_keyword_order(monkeypatch) -> None:
    """两个关键词命中同一批取值 → 每个 id 只留一条, 且留的是**排前面**的那份.

    与列召回那条同款: 排在前面的那条慢、后发的快 —— 按完成顺序回填的实现会翻车.
    """
    monkeypatch.setattr(app_config.recall, "concurrency", 2)
    keywords = ["关键词0", "关键词1"]
    merged = list(set(keywords))
    first_kw, second_kw = merged

    region = {"id": "dim_region.province.华北", "value": f"来自{first_kw}"}
    brand = {"id": "dim_product.brand.华为", "value": "华为"}
    repo = FakeValueRepository(
        delay={first_kw: 0.05, second_kw: 0.005},
        hits={
            first_kw: [region, brand],
            second_kw: [dict(region, value=f"来自{second_kw}"), brand],
        },
    )
    _stub_extension(monkeypatch, _2_3_recall_value, [])
    runtime = FakeRuntime({VALUE_REPO: repo})

    update = await _2_3_recall_value.recall_value(
        {"query": "上个月华北的销售额", "keywords": keywords}, runtime
    )
    values = update["retrieved_values"]

    assert [v["id"] for v in values] == [region["id"], brand["id"]]
    assert values[0]["value"] == f"来自{first_kw}", "留下该是排在前面的那份"
