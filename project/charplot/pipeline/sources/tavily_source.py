"""Tavily 网络搜索源 (Issue 07).

联网搜索增强的主源: 补全知识面 + 交叉验证 (ADR-0002 材料也搜).
key 未配置时源不注册 (build_sources 过滤), 管道降级为无网络增强.
"""

import logging

from ...api import config
from .base import SOURCE_WEB, SearchResult

logger = logging.getLogger(__name__)


class TavilySource:
    """Tavily 网络搜索源 (同步封装, 供检索 agent 工具调用)."""

    name = SOURCE_WEB
    description = (
        "联网搜索互联网资料 (Tavily): 查最新知识/教程/官方文章, "
        "用于补全知识面与交叉验证. 输入自然语言查询"
    )

    def __init__(self):
        from tavily import TavilyClient

        self._client = TavilyClient(api_key=config.TAVILY_API_KEY)

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            resp = self._client.search(
                query, max_results=max_results, search_depth="basic"
            )
        except Exception as exc:
            logger.warning("Tavily 搜索失败 (%s): %s", query, exc)
            return []
        results = []
        for item in resp.get("results", [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", "") or "",
                    url=item.get("url", "") or "",
                    content=(item.get("content") or "")[:3000],
                    source_type=SOURCE_WEB,
                    metadata={"score": item.get("score")},
                )
            )
        return results
