from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from loguru import logger
from pydantic import Field
from rich import print as rprint

"""
--- 使用规范 ---
1.可以在【编译时 / 调用时】配置, 推荐配置在【编译时】
2.若存在 a -> b, 节点a之后的中断和节点b之前的中断只会触发一次, 本质就是同一个中断
3.【中断以超步为单位】, 同一个超步内有中断, 则超步内的所有节点都会中断
"""


class OverAllState(MessagesState):
    result1: str = Field(description="结果1")
    result2: str = Field(description="结果2")


def node_a(state: OverAllState) -> OverAllState:
    logger.info("开始节点a...")
    return {"result1": "test result a"}


def node_b(state: OverAllState) -> OverAllState:
    logger.info("开始节点b...")
    return {"result1": "test result b"}


def node_c(state: OverAllState) -> OverAllState:
    logger.info("开始节点c...")
    return {"result1": "test result c"}


def node_d(state: OverAllState) -> OverAllState:
    logger.info("开始节点d...")
    return {"result2": "test result d"}


def node_e(state: OverAllState) -> OverAllState:
    logger.info("开始节点e...")
    return {"result2": "test result e"}


graph = (
    StateGraph(state_schema=OverAllState)
    .add_node(node_a)
    .add_node(node_b)
    .add_node(node_c)
    .add_node(node_d)
    .add_node(node_e)
    .add_edge(START, "node_a")
    .add_edge("node_a", "node_b")
    .add_edge("node_b", "node_c")
    .add_edge(START, "node_d")
    .add_edge("node_d", "node_e")
    .add_edge("node_e", "node_c")
    .add_edge("node_c", END)
).compile(
    checkpointer=InMemorySaver(),
    interrupt_before=["node_a", "node_b"],
    interrupt_after=["node_a", "node_b"],
)

config = {"configurable": {"thread_id": "langgraph_4_4_"}}

# interrupt_before - node_a
rprint(f"run1: {graph.invoke({}, config=config)}")

# interrupt_after - node_a == interrupt_before - node_b
rprint(f"run2: {graph.invoke(None, config=config)}")

# interrupt_after - node_b
rprint(f"run3: {graph.invoke(None, config=config)}")

# OVER
rprint(f"run4: {graph.invoke(None, config=config)}")
