"""知识库检索源 (Issue 10 接入, SPEC §7.1) - Milvus 混合检索 + rerank.

实现 SearchSource 协议: KbSource(kb_id) 按知识库检索, 返回统一
SearchResult (content=片段原文, metadata 带来源/doc_id/chunk_index,
供解构/出题引用与来源展示, QA.md Q7). 检索链路在 rag/retriever.py
(rewrite → 混合检索 + 软删过滤 → rerank), 本类只做协议适配.

接入方式: 知识库驱动旅程 (Issue 11) 时在 build_sources 注册
KbSource(journey 的 kb_id); 当前可独立实例化调用 (调试/接口测试).
检索失败 (Milvus/Django 不可达) 抛 RuntimeError, 由调用方降级.
"""

import logging

from ...rag.retriever import search_kb
from .base import SOURCE_KB, SearchResult

logger = logging.getLogger(__name__)


class KbSource:
    """知识库检索源: 构造时绑定 kb_id, search 走完整 RAG 检索链路."""

    name = SOURCE_KB
    description = (
        "检索已建知识库 (Milvus): 管理员预建主题文档, 检索命中返回原文片段 "
        "(混合检索 + 精排, 软删文档自动过滤)"
    )

    def __init__(self, kb_id: int):
        self.kb_id = kb_id

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        chunks = search_kb(self.kb_id, query, top_k=max_results)
        return [
            SearchResult(
                title=chunk["title"] or chunk["filename"],
                url="",
                content=chunk["content"],
                source_type=SOURCE_KB,
                metadata={
                    "kb_id": self.kb_id,
                    "doc_id": chunk["doc_id"],
                    "filename": chunk["filename"],
                    "chunk_index": chunk["chunk_index"],
                    "score": chunk.get("score", 0.0),
                },
            )
            for chunk in chunks
        ]
