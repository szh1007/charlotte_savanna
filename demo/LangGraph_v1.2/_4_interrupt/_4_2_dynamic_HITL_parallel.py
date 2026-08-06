import time

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command, interrupt
from pydantic import Field
from rich import print as rprint


class OverAllState(MessagesState):
    username: str = Field(description="用户名称")
    age: int = Field(description="用户年龄")


def node_a(state: OverAllState) -> OverAllState:
    username = interrupt("请输入用户名: ")
    return {"username": username}


def node_b(state: OverAllState) -> OverAllState:
    time.sleep(1)
    age = interrupt("请输入年龄: ")
    return {"age": int(age)}


graph = (
    StateGraph(state_schema=OverAllState)
    .add_node(node_a)
    .add_node(node_b)
    .add_edge(START, "node_a")
    .add_edge(START, "node_b")
    .add_edge("node_a", END)
    .add_edge("node_b", END)
).compile(
    checkpointer=InMemorySaver(),
)

config = {
    "configurable": {"thread_id": "langgraph_4_2_"},
}

interrupt_response = graph.invoke({}, config=config)

resume_map = {}
for res in interrupt_response["__interrupt__"]:
    key, value = res.id, res.value
    user_input = input(value)

    if "年龄" in value:
        resume_map[key] = int(user_input)
    else:
        resume_map[key] = user_input

resume_response = graph.invoke(Command(resume=resume_map), config=config)
rprint(resume_response)
