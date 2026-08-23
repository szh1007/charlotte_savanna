"""RAG 全链路测试 (Issue 10, DESIGN.md §7 步骤 10 / SPEC §7.2).

覆盖: query rewriting (LLM 改写 / 失败降级原查询) / Milvus filter 软删
表达式 / 混合检索全链路 (rewrite → embed → 软删过滤 → hybrid → rerank,
Top-K 精排) / /ai/kb/search 端点契约 / KbSource 协议适配 / 真实索引任务
失败语义 (解析失败 → error + 失败标记). 外部依赖 (embedding 模型 /
Milvus / Django) 经 conftest 假件与 monkeypatch 隔离, 不触网不加载模型.
"""

import pytest
from tests.conftest import KB_CONTENTS, FakeChatModel, wait_task_status
from tests.test_kb_index import start_index
from tests.test_tasks_sse import read_stream

from project.charplot.api import tasks
from project.charplot.pipeline import llm as pipeline_llm
from project.charplot.rag import milvus as rag_milvus
from project.charplot.rag import query_rewrite, retriever
from project.charplot.rag.rerank import NoopReranker

# 预置检索候选 (混合检索返回, score 为融合分)
CANDIDATES = [
    {
        "doc_id": 3,
        "title": "c.md",
        "filename": "c.md",
        "chunk_index": 0,
        "content": "装饰器与闭包结合实现缓存",
        "score": 0.9,
    },
    {
        "doc_id": 1,
        "title": "a.txt",
        "filename": "a.txt",
        "chunk_index": 0,
        "content": "装饰器是函数包装机制",
        "score": 0.8,
    },
    {
        "doc_id": 2,
        "title": "b.md",
        "filename": "b.md",
        "chunk_index": 2,
        "content": "闭包捕获词法作用域",
        "score": 0.6,
    },
]


def _patch_search_deps(monkeypatch, deleted_ids=(3,), hits=None):
    """检索链路隔离: 软删集合 + 假 rerank (patch retriever 模块级绑定)."""
    monkeypatch.setattr(retriever, "_deleted_doc_ids", lambda kb_id: list(deleted_ids))
    monkeypatch.setattr(retriever, "get_reranker", lambda: NoopReranker())
    return hits


# ---- query rewriting ----


def test_rewrite_query_degrades_on_llm_failure():
    """LLM 不可用 (FakeChatModel 未知 prompt 抛异常): 降级原查询不抛."""
    assert query_rewrite.rewrite_query("装饰器咋用") == "装饰器咋用"


def test_rewrite_query_uses_llm_result(monkeypatch):
    """LLM 改写生效 (FakeChatModel sequence 注入改写结果)."""
    monkeypatch.setattr(
        pipeline_llm,
        "get_chat_model",
        lambda: FakeChatModel(sequence=[("改写", "Python 装饰器 语法 应用场景")]),
    )
    assert query_rewrite.rewrite_query("装饰器咋用") == "Python 装饰器 语法 应用场景"


def test_rewrite_query_can_be_disabled(monkeypatch):
    """CHARPLOT_QUERY_REWRITE=false: 不调 LLM 直接返回原查询."""
    monkeypatch.setattr("project.charplot.api.config.QUERY_REWRITE", False)
    assert query_rewrite.rewrite_query("装饰器") == "装饰器"


# ---- Milvus filter 软删表达式 ----


def test_filter_expr_no_deleted():
    assert rag_milvus._build_filter_expr([]) == "valid == true"


def test_filter_expr_with_deleted():
    assert (
        rag_milvus._build_filter_expr([2, 5])
        == "valid == true and doc_id not in [2, 5]"
    )


# ---- 混合检索全链路 ----


def test_search_kb_full_chain(monkeypatch, fake_rag_deps):
    """全链路: 改写 query → embed → 软删过滤 → 混合检索 → rerank Top-K."""
    embedder, milvus_client = fake_rag_deps
    milvus_client._hits = CANDIDATES  # 预置混合检索结果
    _patch_search_deps(monkeypatch, deleted_ids=(3,))

    chunks = retriever.search_kb(1, "装饰器", top_k=2)

    # embed 输入是改写后的 query (FakeChatModel 失败降级 = 原 query)
    assert embedder.last_query == "装饰器"
    # 混合检索: 软删集合传入 (filter 排除 doc 3)
    call = milvus_client.hybrid_calls[-1]
    assert call["collection_name"] == "cp_kb_1"
    assert call["reqs"][0].expr == "valid == true and doc_id not in [3]"
    # rerank 按 score 降序取 Top-2 (doc 3 已被过滤, 只剩 1/2)
    assert [c["doc_id"] for c in chunks] == [1, 2]
    assert chunks[0]["content"] == "装饰器是函数包装机制"
    assert chunks[0]["chunk_index"] == 0


def test_search_kb_soft_deleted_excluded_immediately(monkeypatch, fake_rag_deps):
    """软删立即生效: 软删集合变化 → filter 同步变化 (无需重建)."""
    _, milvus_client = fake_rag_deps
    milvus_client._hits = CANDIDATES

    _patch_search_deps(monkeypatch, deleted_ids=(1, 2, 3))
    retriever.search_kb(1, "装饰器")
    assert (
        milvus_client.hybrid_calls[-1]["reqs"][0].expr
        == "valid == true and doc_id not in [1, 2, 3]"
    )

    # 恢复 doc 2 (软删集合变小) → 重新命中
    _patch_search_deps(monkeypatch, deleted_ids=(1,))
    retriever.search_kb(1, "装饰器")
    assert (
        milvus_client.hybrid_calls[-1]["reqs"][0].expr
        == "valid == true and doc_id not in [1]"
    )


def test_search_kb_empty_query_raises(fake_rag_deps):
    with pytest.raises(ValueError):
        retriever.search_kb(1, "   ")


# ---- /ai/kb/search 端点 ----


def test_search_api_endpoint(client, monkeypatch, fake_rag_deps):
    """端点契约: {kb_id, query} → {chunks[]} (片段带来源 metadata)."""
    _, milvus_client = fake_rag_deps
    milvus_client._hits = CANDIDATES
    _patch_search_deps(monkeypatch, deleted_ids=())

    resp = client.post("/ai/kb/search", json={"kb_id": 1, "query": "装饰器"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["chunks"]) == 3  # 无软删 → 全量候选
    chunk = body["chunks"][0]
    assert set(chunk) == {
        "doc_id",
        "title",
        "filename",
        "chunk_index",
        "content",
        "score",
    }
    assert chunk["content"] == "装饰器与闭包结合实现缓存"


def test_search_api_empty_query_400(client, fake_rag_deps):
    resp = client.post("/ai/kb/search", json={"kb_id": 1, "query": ""})
    assert resp.status_code == 400


# ---- KbSource 协议适配 (管道检索源, Issue 11 接入位) ----


def test_kb_source_mapping(monkeypatch):
    """KbSource: search 走 rag 检索, 结果映射为统一 SearchResult."""
    from project.charplot.pipeline.sources import kb_source as kb_source_mod

    chunks = [
        {
            "doc_id": 7,
            "title": "主题文档",
            "filename": "a.md",
            "chunk_index": 1,
            "content": "片段原文",
            "score": 0.9,
        }
    ]
    monkeypatch.setattr(
        kb_source_mod, "search_kb", lambda kb_id, query, top_k=None: chunks
    )

    results = kb_source_mod.KbSource(3).search("装饰器", max_results=5)
    assert len(results) == 1
    result = results[0]
    assert result.source_type == "kb"
    assert result.content == "片段原文"
    assert result.title == "主题文档"
    assert result.metadata == {
        "kb_id": 3,
        "doc_id": 7,
        "filename": "a.md",
        "chunk_index": 1,
        "score": 0.9,
    }


# ---- 真实索引任务: 失败语义 ----


def test_index_task_parse_failure_marks_failed(client, mock_kb_endpoints, monkeypatch):
    """任一文档解析失败 → error + mark_kb_index_failed (前端可重试)."""
    calls = mock_kb_endpoints

    async def fake_fetch(doc_id):
        if int(doc_id) == 2:
            return "bad.pdf", b"%PDF-1.4 broken file"
        filename, text = KB_CONTENTS[int(doc_id)]
        return filename, text.encode()

    monkeypatch.setattr(tasks, "fetch_kb_document_content", fake_fetch)
    task_id = start_index(client)
    assert wait_task_status(client, task_id, "error")

    events = read_stream(client, task_id)
    assert events[-1][2]["stage"] == "error"
    assert "索引失败" in events[-1][2]["message"]
    assert len(calls["failed"]) == 1
    assert calls["failed"][0][0] == 1  # kb_id
    assert calls["save"] == []
