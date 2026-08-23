"""检索源抽象 (Issue 07, SPEC §7.1).

可插拔检索源: 网络搜索 (Tavily) / Context7 官方文档 / 输入文档材料 /
知识库 (预留, Issue 10 接入 Milvus). 统一管道 (ADR-0002) 中搜索增强
阶段通过 DeepAgents subagent 调用各源工具; 源按配置启用 (无 Tavily
key 时自动降级, 不影响其余源).
"""

import logging
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)

# 源类型标识 (SearchResult.source_type / 工具名后缀)
SOURCE_WEB = "web"
SOURCE_DOCS = "docs"
SOURCE_DOCUMENT = "document"
SOURCE_KB = "kb"


@dataclass
class SearchResult:
    """单条检索结果 (统一结构, 供 LLM 阅读与解构阶段引用)."""

    title: str
    url: str = ""
    content: str = ""
    source_type: str = SOURCE_WEB
    metadata: dict = field(default_factory=dict)


class SearchSource(Protocol):
    """检索源协议: 实现 search 即插即用.

    实现类挂到 SearchSourceProtocol 的工具封装 (agents/tools.py) 后,
    DeepAgents 检索 subagent 即可调用; 新增源只需实现本协议并在
    build_sources 注册.
    """

    name: str  # 源标识 (web / docs / document / kb)
    description: str  # 工具描述 (LLM 决定何时调用)

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """执行检索, 返回统一 SearchResult 列表 (失败降级为空列表并告警)."""
        ...
