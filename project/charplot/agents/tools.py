"""检索源 → LangChain 工具封装 (Issue 07).

把可插拔检索源 (pipeline/sources) 包成 @tool 挂给 DeepAgents 检索
subagent; 工具名 = <源名>_search, 描述来自源定义 (LLM 据此决定调用).
"""

import logging
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _result_to_dict(result) -> dict[str, Any]:
    return {
        "title": result.title,
        "url": result.url,
        "content": result.content,
        "source_type": result.source_type,
    }


def build_search_tools(sources: list) -> list:
    """把源列表转为工具列表 (每个源一个 {name}_search 工具).

    LangChain 1.3 的 @tool 首个位置参数为工具名; 闭包用默认参数绑定
    源实例, 避免循环变量捕获 (全部工具指到最后一个源).
    """
    tools = []
    for src in sources:
        tool_name = f"{src.name}_search"

        @tool(tool_name, description=src.description)
        def search_tool(query: str, max_results: int = 5, _src=src) -> list[dict]:
            results = _src.search(query, max_results=max_results)
            return [_result_to_dict(r) for r in results]

        tools.append(search_tool)
    return tools
