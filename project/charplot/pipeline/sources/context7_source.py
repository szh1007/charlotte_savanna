"""Context7 官方文档检索源 (Issue 07).

Context7 Public API v2 (https://context7.com/docs/openapi.json):
  1. GET /api/v2/libs/search  -> 按库名解析出匹配库 (LLM 智能排序)
  2. GET /api/v2/context      -> 按查询取该库最新文档片段 (txt 格式)

匿名可用 (限速 200 req/min), 失败降级为空结果; 编程主题 (框架/库)
搜索的核心增强源.
"""

import logging
import re

import httpx

from ...api import config
from .base import SOURCE_DOCS, SearchResult

logger = logging.getLogger(__name__)

_SOURCE_URL_RE = re.compile(r"^Source:\s*(https?://\S+)", re.IGNORECASE)


class Context7Source:
    """Context7 官方文档检索源 (同步封装)."""

    name = SOURCE_DOCS
    description = (
        "检索编程框架/库的最新官方文档 (Context7): 查 API 用法/最新语法/"
        "版本差异, 输入框架或库名相关的查询"
    )

    def __init__(self):
        self._base = config.CONTEXT7_BASE_URL
        self._max_docs = config.CONTEXT7_MAX_DOCS

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            libraries = self._resolve_libraries(query)
        except httpx.HTTPError as exc:
            logger.warning("Context7 库解析失败 (%s): %s", query, exc)
            return []
        results: list[SearchResult] = []
        # 取前 2 个最相关库查询文档 (每库内容已聚合多条片段)
        for lib in libraries[: min(2, max_results)]:
            results.extend(self._query_library(lib, query, max_results))
            if len(results) >= max_results * 2:
                break
        return results[: max_results * 2]

    def _resolve_libraries(self, query: str) -> list[dict]:
        resp = httpx.get(
            f"{self._base}/v2/libs/search",
            params={"libraryName": query, "query": query, "fast": "true"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def _query_library(
        self, lib: dict, query: str, max_results: int
    ) -> list[SearchResult]:
        lib_id = lib.get("id", "")
        if not lib_id:
            return []
        try:
            resp = httpx.get(
                f"{self._base}/v2/context",
                params={
                    "libraryId": lib_id,
                    "query": query,
                    "type": "txt",
                    "fast": "true",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Context7 文档查询失败 (%s): %s", lib_id, exc)
            return []
        return self._split_snippets(lib, resp.text)

    def _split_snippets(self, lib: dict, text: str) -> list[SearchResult]:
        """把 docs 响应按 '----' 分隔符切成多条片段, 提取来源 URL."""
        title = lib.get("title") or lib.get("id", "")
        results: list[SearchResult] = []
        for block in text.split("-" * 32):
            block = block.strip()
            if not block:
                continue
            url = ""
            for line in block.splitlines()[:3]:
                m = _SOURCE_URL_RE.match(line.strip())
                if m:
                    url = m.group(1)
                    break
            first_line = block.splitlines()[0][:40] if block.splitlines() else title
            results.append(
                SearchResult(
                    title=f"{title}: {first_line}",
                    url=url,
                    content=block[:3000],
                    source_type=SOURCE_DOCS,
                    metadata={"library_id": lib.get("id")},
                )
            )
        return results
