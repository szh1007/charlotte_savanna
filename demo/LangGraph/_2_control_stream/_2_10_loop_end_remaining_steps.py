from typing import Literal

from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.managed import RemainingSteps
from loguru import logger
from pydantic import Field


class OverAllState(MessagesState):
    remaining_steps: RemainingSteps = Field(..., description="剩余步骤数")


def loop_node(state: OverAllState, config: RunnableConfig) -> OverAllState:
    """循环节点"""
    cur_step = config["metadata"]["langgraph_step"]
    remaining_steps = state["remaining_steps"]
    logger.info(f"当前步骤: {cur_step}, 剩余步骤数: {remaining_steps}")


def router(state: OverAllState) -> Literal["loop", "END"]:
    """路由函数"""
    if state["remaining_steps"] > 3:
        return "loop"
    else:
        logger.info(f"剩余步骤数: {state['remaining_steps']}")
        return "END"


builder = StateGraph(state_schema=OverAllState)
builder.add_node(loop_node)

builder.add_edge(START, "loop_node")
builder.add_conditional_edges(
    "loop_node",
    router,
    path_map={"loop": "loop_node", "END": "__end__"},
)

graph = builder.compile()
graph.invoke({}, config={"recursion_limit": 10})
