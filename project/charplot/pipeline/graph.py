"""LangGraph StateGraph 编排 (Issue 07).

四阶段串行编排: parse → analyze → search → deconstruct → END.
每个节点开头通过 emit 回调上报阶段事件 (阶段名/进度/消息, 与
Issue 03 SSE 契约一致, 每阶段恰好一次事件).

节点失败向上抛异常 → 任务系统 (tasks.py) 转 error 事件 + 失败标记,
前端可重新 POST /ai/pipeline 重试 (幂等落库).
"""

import logging

from langgraph.graph import END, START, StateGraph

from .stages.analyze import analyze_node
from .stages.deconstruct import deconstruct_node
from .stages.parse import parse_node
from .stages.search import search_node
from .types import PipelineState

logger = logging.getLogger(__name__)

STAGES = ["parsing", "analyzing", "searching", "deconstructing"]
STAGE_PROGRESS = {"parsing": 15, "analyzing": 35, "searching": 60, "deconstructing": 90}
STAGE_MESSAGES = {
    "parsing": "正在解析输入内容…",
    "analyzing": "正在分析主题结构…",
    "searching": "正在搜索相关知识…",
    "deconstructing": "正在解构知识图谱…",
}


def build_graph(emit):
    """构建带阶段上报的四阶段 StateGraph (emit 闭包注入, 见 run_pipeline).

    每个节点包一层 emit 上报后委托真实节点; 节点内真实执行对应阶段工作.
    """

    async def parse_node_emitted(state):
        await emit("parsing", STAGE_PROGRESS["parsing"], STAGE_MESSAGES["parsing"])
        return await parse_node(state)

    async def analyze_node_emitted(state):
        await emit(
            "analyzing", STAGE_PROGRESS["analyzing"], STAGE_MESSAGES["analyzing"]
        )
        return await analyze_node(state)

    async def search_node_emitted(state):
        await emit(
            "searching", STAGE_PROGRESS["searching"], STAGE_MESSAGES["searching"]
        )
        return await search_node(state)

    async def deconstruct_node_emitted(state):
        await emit(
            "deconstructing",
            STAGE_PROGRESS["deconstructing"],
            STAGE_MESSAGES["deconstructing"],
        )
        return await deconstruct_node(state)

    builder = StateGraph(PipelineState)
    builder.add_node("parse", parse_node_emitted)
    builder.add_node("analyze", analyze_node_emitted)
    builder.add_node("search", search_node_emitted)
    builder.add_node("deconstruct", deconstruct_node_emitted)
    builder.add_edge(START, "parse")
    builder.add_edge("parse", "analyze")
    builder.add_edge("analyze", "search")
    builder.add_edge("search", "deconstruct")
    builder.add_edge("deconstruct", END)
    return builder.compile()


async def run_pipeline(inp, emit) -> dict:
    """执行知识管道, 返回契约图谱 dict (CONTRACT.md §1, Issue 03 签名不变).

    emit(stage, progress, message) async 回调上报进度 (任务系统写 Redis + SSE);
    每个阶段真实执行对应工作, 不再有 stub 模拟延迟.
    """
    graph = build_graph(emit)
    result = await graph.ainvoke({"inp": inp})
    return result["graph"]
