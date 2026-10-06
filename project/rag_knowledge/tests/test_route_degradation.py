"""C02 回归: 单路召回为空/失败时**降级**, 而不是把整条链变成 500.

修复前的形态有两处:
- `rrf_service._validate_data` 要求两路都非空, 否则 `raise ValueError`
  → Tavily 挂了, 或知识库里没有该主体, 整条问答链直接失败;
- `rerank_service._validate_data` 连 web 一路也要求非空 —— 评测里必须塞一条
  **假的联网文档**才能跑起来, 而那条假文档会真的进精排池抢名次.

外加 `milvus_utils.hybrid_search` 失败时返回 None, 三个调用方却一律写
`response[0]` → 崩在 `'NoneType' object is not subscriptable`, 真正错因被盖住.
"""

from __future__ import annotations

import pytest

from app.rag.query import rerank_service, rrf_service
from app.shared.runtime.route_isolation import isolate_route

# ---------------------------------------------------------------------------
# RRF: 单路为空不再是错误
# ---------------------------------------------------------------------------


# 断崖窗口钉住: 下面这些用例测的是**判据本身** (落差会不会被检查到、全 0 会不会炸),
# 与"线上当前交付几条"无关. 2026-10-06 `RERANK_MAX_TOPK` 由 8 调到 1 之后, 依赖
# "窗口够宽"的用例全挂了 —— 根因就是它们读的是线上调参值.
CLIFF_MAX, CLIFF_MIN = 8, 1


@pytest.fixture(autouse=True)
def _pin_cliff_window(monkeypatch):
    monkeypatch.setattr(rerank_service, "RERANK_MAX_TOPK", CLIFF_MAX)
    monkeypatch.setattr(rerank_service, "RERANK_MIN_TOPK", CLIFF_MIN)


def test_rrf_tolerates_a_single_empty_route():
    """一路为空 → 按「这一路没贡献」继续, 不抛."""
    state = {
        "embedding_chunks": [{"chunk_id": "a", "content": "x"}],
        "hyde_embedding_chunks": [],
    }

    result = rrf_service.fuse_by_rrf(state)

    assert [chunk["chunk_id"] for chunk in result["rrf_chunks"]] == ["a"]


def test_rrf_with_both_routes_empty_returns_empty():
    """两路都空也不抛 —— 交给作答节点走「检索不到」的正常分支."""
    result = rrf_service.fuse_by_rrf(
        {"embedding_chunks": [], "hyde_embedding_chunks": []}
    )

    assert result["rrf_chunks"] == []


# ---------------------------------------------------------------------------
# 精排: 只有 rewritten_query 是真必需
# ---------------------------------------------------------------------------


def test_rerank_skips_scoring_when_both_routes_are_empty():
    """两路都空 → 跳过打分, reranked_docs 置空 (不把空列表喂给模型)."""
    state = {"rewritten_query": "怎么退货", "rrf_chunks": [], "web_search_docs": []}

    result = rerank_service.rerank_documents(state)

    assert result["reranked_docs"] == []


def test_rerank_still_requires_a_query():
    """反例: rewritten_query 是真必需 —— 没有它就无从打分, 该报错就报错."""
    try:
        rerank_service.rerank_documents(
            {"rewritten_query": "", "rrf_chunks": [], "web_search_docs": []}
        )
    except ValueError as exc:
        assert "rewritten_query" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("rewritten_query 为空时应当报错")


def test_web_url_survives_the_merge():
    """联网结果的链接必须带进精排池 —— 否则「可溯源」在 web 那一路是空的."""
    merged = rerank_service._merge_rrf_and_web(
        [],
        [{"title": "t", "text": "x", "url": "https://example.com/a"}],
    )

    assert merged[0]["url"] == "https://example.com/a"


# ---------------------------------------------------------------------------
# 除零
# ---------------------------------------------------------------------------


def test_dynamic_topk_survives_all_zero_scores():
    """分数全 0 时没有「百分比」可言 —— 此前直接相除会 ZeroDivisionError."""
    merged = [{"score": 0.0} for _ in range(6)]

    assert rerank_service._dynamic_topk(merged) == merged


# ---------------------------------------------------------------------------
# 单路失败隔离
# ---------------------------------------------------------------------------


def test_isolate_route_returns_the_fallback_instead_of_raising():
    """一路抛异常 → 记 error 日志 + 返回空列表, 不往上冒."""
    calls: list[str] = []

    def boom():
        calls.append("called")
        raise RuntimeError("Milvus 连不上")

    assert isolate_route("向量召回", boom, []) == []
    assert calls == ["called"]


def test_isolate_route_passes_through_on_success():
    assert isolate_route("联网召回", lambda: [1, 2], []) == [1, 2]


# ---------------------------------------------------------------------------
# 作答节点: 「检索不到」不是错误
# ---------------------------------------------------------------------------


def test_answer_node_degrades_instead_of_raising_when_nothing_was_retrieved(
    monkeypatch,
):
    """回归本体: 三路全空时作答节点此前直接 raise → 整条链 500.

    RRF 与精排都降级完之后, 就剩这一站还在抛 —— 修了中间两处却没修终点,
    「降级而不是 500」这句话就还是假的.
    """
    from app.rag.query import answer_service

    def must_not_call_llm(*args, **kwargs):
        raise AssertionError("没有资料可依据时不该叫模型 —— 叫它只会编")

    monkeypatch.setattr(answer_service, "_save_assistant_message", lambda state: None)
    monkeypatch.setattr(answer_service, "_call_llm_create_answer", must_not_call_llm)

    result = answer_service.generate_answer(
        {
            "session_id": "s",
            "item_names": ["某设备"],
            "rewritten_query": "这机器怎么保养",
            "reranked_docs": [],
            "is_stream": False,
        }
    )

    assert result["answer"] == answer_service.NO_RELEVANT_DOC_ANSWER


def test_answer_node_still_requires_a_query(monkeypatch):
    """反例: rewritten_query 是真必需 —— 缺了它该报错就报错."""
    from app.rag.query import answer_service

    try:
        answer_service.generate_answer(
            {"session_id": "s", "item_names": ["x"], "rewritten_query": ""}
        )
    except ValueError as exc:
        assert "rewritten_query" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("rewritten_query 为空时应当报错")
