"""知识库检索源 (预留, Issue 07 仅定义接口).

RAG 知识库 (Milvus 混合检索 + rerank) 在 Issue 10 接入: 届时实现
search 并用 build_sources 注册 (kb_id 由旅程输入携带). 当前不注册,
避免检索 agent 调用空源浪费时间.
"""

import logging

from .base import SOURCE_KB, SearchResult

logger = logging.getLogger(__name__)


class KbSource:
    """知识库检索源占位: 未接入时 search 返回空 (Issue 10 替换实现)."""

    name = SOURCE_KB
    description = (
        "检索已建知识库 (Milvus): 管理员预建主题文档, 检索命中返回原文片段 "
        "(预留源, 接入后启用)"
    )

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        logger.info("知识库检索源未接入 (Issue 10 Milvus), 查询忽略: %s", query)
        return []
