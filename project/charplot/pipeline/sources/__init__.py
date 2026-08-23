"""检索源包 (Issue 07): 可插拔源抽象 + 按配置构建.

统一管道 (ADR-0002) 搜索增强阶段使用的源集合; 新增检索源只需实现
base.SearchSource 协议并在此注册.
"""

import logging

from ...api import config
from .base import SOURCE_DOCS, SOURCE_DOCUMENT, SOURCE_KB, SOURCE_WEB, SearchResult
from .context7_source import Context7Source
from .document_source import DocumentSource
from .tavily_source import TavilySource

logger = logging.getLogger(__name__)


def build_sources(material_text: str | None = None) -> list:
    """构建启用的检索源列表 (按配置可插拔).

    - Tavily: 配置了 TAVILY_API_KEY 才启用 (联网增强主源)
    - Context7: 恒启用 (公开 API, 失败自动降级)
    - 文档材料: 输入非空材料时启用 (用户原文可回查)
    - 知识库: 预留 (Issue 10 接入 Milvus 后启用)
    """
    sources = []
    if config.TAVILY_API_KEY:
        sources.append(TavilySource())
    else:
        logger.warning("未配置 TAVILY_API_KEY, 网络搜索源已降级跳过")
    sources.append(Context7Source())
    if material_text and material_text.strip():
        sources.append(DocumentSource(material_text))
    else:
        logger.info("无输入材料文本, 文档材料检索源跳过")
    # 知识库源预留: Issue 10 接入 Milvus 后追加 KbSource()
    return sources


__all__ = [
    "SOURCE_DOCS",
    "SOURCE_DOCUMENT",
    "SOURCE_KB",
    "SOURCE_WEB",
    "SearchResult",
    "build_sources",
]
