"""检索 subagent (Issue 07 阶段 3: searching 的 DeepAgents 执行体).

DeepAgents subagent 承担检索环节: 挂各检索源工具 (网络/Context7/文档),
自主编排查询策略, 结构化输出检索报告 (response_format 约束, 便于
下游解构阶段消费).

惰性构建 (build_search_agent): 避免 import 阶段初始化 LLM/源, 测试
可 monkeypatch 源集合与模型.
"""

import logging

from deepagents import create_deep_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from ..prompt.search import SEARCH_AGENT_PROMPT
from .tools import build_search_tools

logger = logging.getLogger(__name__)


class SearchItem(BaseModel):
    """单条检索结果 (结构化报告元素)."""

    title: str
    url: str = ""
    content: str
    source_type: str = "web"


class SearchReport(BaseModel):
    """检索报告 (response_format 结构化输出)."""

    topic: str
    queries: list[str] = Field(default_factory=list)
    results: list[SearchItem] = Field(default_factory=list)
    takeaway: str = ""


def build_search_agent(sources: list):
    """构建检索 subagent (惰性: 每次调用按当前源集合构建).

    pipeline.llm 延迟导入: agents 被 pipeline.stages 顶层 import,
    顶部直接 import pipeline 会形成循环 (pipeline 半初始化).
    """
    from ..pipeline import llm

    model = llm.get_chat_model()
    # ToolStrategy (function calling 路径): DeepSeek 不支持 json_schema
    # response_format (ProviderStrategy 默认会 400)
    return create_deep_agent(
        model=model,
        tools=build_search_tools(sources),
        system_prompt=SEARCH_AGENT_PROMPT,
        response_format=ToolStrategy(schema=SearchReport),
        name="search_researcher",
    )


async def run_search_agent(
    sources: list, topic: str, queries: list[str]
) -> SearchReport:
    """执行检索: subagent 按建议查询自主检索, 返回结构化报告.

    queries 为空时 (输入无分析建议) 仍给主题, agent 自行组织查询.
    """
    agent = build_search_agent(sources)
    # 全角括号为中文提示文案 (RUF001 刻意保留)
    suggested = "、".join(queries) if queries else "（由你根据主题自行组织）"  # noqa: RUF001
    prompt = f"学习主题: {topic}\n建议检索查询: {suggested}"
    # recursion_limit 覆盖默认值: 检索需多轮工具调用, 10 层默认值不足
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]},
        config={"recursion_limit": 20},
    )
    # agent.ainvoke 返回 dict (AgentState), 结构化输出在 structured_response 键
    report = result.get("structured_response")
    if not isinstance(report, SearchReport):
        report = _parse_text_report(result, topic, queries)
    if not isinstance(report, SearchReport):
        # 结构化输出缺失 (模型未走 function calling) 时兜底为最小报告
        logger.warning("检索 agent 未产出结构化报告, 使用空报告兜底")
        return SearchReport(topic=topic, queries=queries)
    return report


def _parse_text_report(result, topic: str, queries: list[str]) -> SearchReport | None:
    """兜底: 解析 agent 最后一条文本消息中的 JSON 为 SearchReport.

    DeepSeek 偶发不遵守 function calling 输出 (直接返回文本), 工具调用
    历史仍在 messages 中, 文本里通常含检索结论 JSON.
    """
    # json_utils 延迟导入: 顶层 import pipeline 子模块会触发循环 (同 llm)
    from ..pipeline.json_utils import extract_json

    messages = result.get("messages") or []
    for msg in reversed(messages):
        content = getattr(msg, "content", "") or ""
        if not isinstance(content, str) or "{" not in content:
            continue
        try:
            return SearchReport.model_validate(extract_json(content))
        except ValueError:
            continue
    return None
