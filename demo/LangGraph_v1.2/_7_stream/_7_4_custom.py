import time

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.runtime import Runtime
from pydantic import Field
from rich import print as rprint

"""
stream_mode=["custom"]

节点 - Runtime.stream_writer(...)
工具 - ToolRuntime.stream_writer(...)
"""


class OverAllState(MessagesState):
    initial_state: dict = Field(description="初始状态")
    output_a: str = Field(description="模型输出a")
    output_b: str = Field(description="模型输出b")


def node_a(state: OverAllState, runtime: Runtime) -> OverAllState:
    time.sleep(1)
    writer = runtime.stream_writer
    writer("node_a running...")
    return {"output_a": "test output a"}


def node_b(state: OverAllState, runtime: Runtime) -> OverAllState:
    time.sleep(1)
    writer = runtime.stream_writer
    writer("node_b running...")
    return {"output_b": "test output b"}


graph = (
    StateGraph(OverAllState)
    .add_node(node_a)
    .add_node(node_b)
    .add_edge(START, "node_a")
    .add_edge("node_a", "node_b")
    .add_edge("node_b", END)
).compile()

for i, chunk in enumerate(
    graph.stream(
        {"initial_state": {"初始状态"}},
        stream_mode=["custom"],
        # version="v2",
    )
):
    rprint(f"{i}: {chunk}")
    rprint("-" * 100)
