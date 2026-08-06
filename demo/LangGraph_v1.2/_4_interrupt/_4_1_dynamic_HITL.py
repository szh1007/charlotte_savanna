from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command, interrupt
from pydantic import Field
from rich import print as rprint


class OverAllState(MessagesState):
    username: str = Field(description="用户名称")


def test_node(state: OverAllState) -> OverAllState:
    username = interrupt("请输入用户名: ")
    return {"username": username}


graph = (
    StateGraph(state_schema=OverAllState)
    .add_node(test_node)
    .add_edge(START, "test_node")
    .add_edge("test_node", END)
).compile(
    checkpointer=InMemorySaver(),
)

config = {
    "configurable": {"thread_id": "langgraph_4_1_"},
}

# interrupt
interrupt_response = graph.invoke({}, config=config)
interrupt_prompt = interrupt_response["__interrupt__"][0].value

# resume
username = input(interrupt_prompt)
resume_response = graph.invoke(Command(resume=username), config=config)
rprint(resume_response)
