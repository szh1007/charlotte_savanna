import time

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command, interrupt
from pydantic import Field
from rich import print as rprint


class OverAllState(MessagesState):
    initial_state: dict = Field(description="初始状态")
    output_node_1_1: str = Field(description="节点1-1的输出")
    output_node_1_2: str = Field(description="节点1-2的输出")
    output_node_2: str = Field(description="节点2的输出")


def node_1_1(state: OverAllState) -> OverAllState:
    time.sleep(1)
    return {"output_node_1_1": "test output node_1_1"}


def node_1_2(state: OverAllState) -> OverAllState:
    time.sleep(1)
    return {"output_node_1_2": "test output node_1_2"}


def node_2(state: OverAllState) -> OverAllState:
    time.sleep(1)
    x = interrupt("hello")
    return {"output_node_2": x}


graph = (
    StateGraph(OverAllState)
    .add_node(node_1_1)
    .add_node(node_1_2)
    .add_node(node_2)
    .add_edge(START, "node_1_1")
    .add_edge(START, "node_1_2")
    .add_edge(["node_1_1", "node_1_2"], "node_2")
    .add_edge("node_2", END)
).compile(checkpointer=InMemorySaver())

config = {
    "configurable": {"thread_id": "langgraph_7_3_"},
}

for i, chunk in enumerate(
    graph.stream(
        {"initial_state": {"初始状态"}},
        config=config,
        # stream_mode=["checkpoints"],
        # stream_mode=["tasks"],
        stream_mode=["debug"],
        version="v2",
    )
):
    rprint(f"{i}: {chunk}")
    rprint("-" * 100)

for i, chunk in enumerate(
    graph.stream(
        Command(resume="xxx"),
        config=config,
        # stream_mode=["checkpoints"],
        # stream_mode=["tasks"],
        stream_mode=["debug"],
        version="v2",
    )
):
    rprint(f"{i}: {chunk}")
    rprint("-" * 100)
