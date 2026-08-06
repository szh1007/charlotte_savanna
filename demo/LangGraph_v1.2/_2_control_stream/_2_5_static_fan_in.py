from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, StateGraph
from langgraph.graph.message import MessagesState
from loguru import logger
from rich import print as rprint


class EmptyState(MessagesState):
    pass


def node1(state: EmptyState, config: RunnableConfig) -> EmptyState:
    cur_step = config["metadata"]["langgraph_step"]
    logger.info(f"Node 1: Current step is {cur_step}")
    return {}


def node2(state: EmptyState, config: RunnableConfig) -> EmptyState:
    cur_step = config["metadata"]["langgraph_step"]
    logger.info(f"Node 2: Current step is {cur_step}")
    return {}


def node3(state: EmptyState, config: RunnableConfig) -> EmptyState:
    cur_step = config["metadata"]["langgraph_step"]
    logger.info(f"Node 3: Current step is {cur_step}")
    return {}


def node4(state: EmptyState, config: RunnableConfig) -> EmptyState:
    cur_step = config["metadata"]["langgraph_step"]
    logger.info(f"Node 4: Current step is {cur_step}")
    return {}


def node5(state: EmptyState, config: RunnableConfig) -> EmptyState:
    cur_step = config["metadata"]["langgraph_step"]
    logger.info(f"Node 5: Current step is {cur_step}")
    return {}


builder = StateGraph(state_schema=EmptyState)
builder.add_node(node1)
builder.add_node(node2)
builder.add_node(node3)
builder.add_node(node4)
builder.add_node(node5)

builder.add_edge(START, "node1")
builder.add_edge("node1", "node2")
builder.add_edge("node1", "node3")
builder.add_edge("node3", "node4")

# 或关系: 任一节点执行完后, 即可执行下一个节点
# builder.add_edge("node2", "node5")
# builder.add_edge("node4", "node5")

# 与关系: 所有节点执行完后, 才能执行下一个节点
builder.add_edge(["node2", "node4"], "node5")

graph = builder.compile()
rprint(graph.get_graph().draw_mermaid())

graph.invoke({})
