"""检索源包 (Issue 07): 可插拔源抽象 + 按配置构建.

统一管道 (ADR-0002) 搜索增强阶段使用的源集合; 新增检索源只需实现
base.SearchSource 协议并在此注册. Issue 11: 知识库旅程 (kb_id 非空)
以知识库为主内容 (QA.md Q8), 仅注册 KbSource, 不联网.
"""

import logging

from ...api import config
from .base import SOURCE_DOCS, SOURCE_DOCUMENT, SOURCE_KB, SOURCE_WEB, SearchResult
from .context7_source import Context7Source
from .document_source import DocumentSource
from .kb_source import KbSource
from .tavily_source import TavilySource

logger = logging.getLogger(__name__)


def build_sources(material_text: str | None = None, kb_id: int | None = None) -> list:
    """构建启用的检索源列表 (按配置可插拔).

    - 知识库: kb_id 非空时仅返回 KbSource (知识库驱动旅程, 主内容源)
    - Tavily: 配置了 TAVILY_API_KEY 才启用 (联网增强主源)
    - Context7: 恒启用 (公开 API, 失败自动降级)
    - 文档材料: 输入非空材料时启用 (用户原文可回查)
    """
    if kb_id is not None:
        return [KbSource(kb_id)]
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
    return sources


__all__ = [
    "SOURCE_DOCS",
    "SOURCE_DOCUMENT",
    "SOURCE_KB",
    "SOURCE_WEB",
    "SearchResult",
    "build_sources",
]
