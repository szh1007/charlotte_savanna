from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, StateGraph
from langgraph.graph.message import MessagesState
from loguru import logger


class EmptyState(MessagesState):
    pass


def loop_node(state: EmptyState, config: RunnableConfig) -> EmptyState:
    """循环节点"""
    cur_step = config["metadata"]["langgraph_step"]
    logger.info(f"当前步骤: {cur_step}")


builder = StateGraph(state_schema=EmptyState)
builder.add_node(loop_node)
builder.add_edge(START, "loop_node")
builder.add_edge("loop_node", "loop_node")

graph = builder.compile()

try:
    graph.invoke({}, config={"recursion_limit": 10})
except GraphRecursionError as e:
    logger.error(f"循环结束异常, 晁超步数量超出限制: {e}")
