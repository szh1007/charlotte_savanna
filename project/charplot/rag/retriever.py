"""检索门面 (Issue 10, QA.md Q7) - 对外统一入口 {kb_id, query} → {chunks[]}.

全链路: query rewriting (LLM 改写, 失败降级) → 混合检索 (稠密+稀疏,
filter 软删排除) → rerank (必配, 精排取 Top-K) → chunks 输出.

软删立即生效 (Q18c): 检索时实时向 Django 查询软删 doc_id 集合, 构造
filter expr 排除 (CONTRACT §6.6); Django 不可达 → 抛 RuntimeError
(接口层转 503, 不静默返回脏数据).

对外形态: search_kb(kb_id, query, top_k) 返回统一 dict 列表 (与
pipeline/sources 的 SearchResult 同构映射在 KbSource 侧完成).
"""

import logging

from ..api import config
from ..api.django_client import fetch_kb_deleted_doc_ids
from . import milvus
from .embeddings import get_embedder
from .query_rewrite import rewrite_query
from .rerank import get_reranker

logger = logging.getLogger(__name__)


def collection_name(kb_id: int) -> str:
    """Milvus collection 名称 (与 Django 创建时生成规则一致)."""
    return f"cp_kb_{kb_id}"


def _deleted_doc_ids(kb_id: int) -> list[int]:
    """实时取软删 doc_id 集合 (Django 内部端点, 软删立即生效)."""
    return fetch_kb_deleted_doc_ids(kb_id)


def search_kb(kb_id: int, query: str, top_k: int | None = None) -> list[dict]:
    """全链路检索: rewrite → hybrid search (软删过滤) → rerank → Top-K.

    top_k 默认 config.RERANK_TOP_K (精排后条数); 召回量
    config.RETRIEVE_TOP_K (精排前候选).
    """
    query = query.strip()
    if not query:
        raise ValueError("检索 query 不能为空")
    top = top_k or config.RERANK_TOP_K
    rewritten = rewrite_query(query)

    embedder = get_embedder()
    query_vec = embedder.embed_query(rewritten)

    deleted_ids = _deleted_doc_ids(kb_id)
    candidates = milvus.hybrid_search(
        collection_name=collection_name(kb_id),
        query_dense=query_vec["dense"],
        query_sparse=query_vec["sparse"],
        deleted_doc_ids=deleted_ids,
        limit=config.RETRIEVE_TOP_K,
    )

    reranker = get_reranker()
    chunks = reranker.rerank(rewritten, candidates, top)
    logger.info(
        "检索完成 (kb=%s): 召回 %d → 精排 %d", kb_id, len(candidates), len(chunks)
    )
    return chunks
