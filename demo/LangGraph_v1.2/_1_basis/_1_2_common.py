import os

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)


# 官方 SOTA 定义方式 - MessagesState
class OverAllState(MessagesState):
    username: str = Field(description="用户名")
    input: str = Field(description="输入内容")
    output: str = Field(description="输出内容")


class InputState(MessagesState):
    username: str
    input: str


class OutputState(MessagesState):
    username: str
    output: str


def node_start(state: InputState) -> OverAllState:
    return {"messages": [HumanMessage(content=state["input"])]}


def node_llm(state: OverAllState) -> OverAllState:
    response = model.invoke(state["messages"])
    return {"messages": [response], "output": response.content}


def node_end(state: OverAllState) -> OutputState:
    return {"username": state["username"] + "-end", "output": state["output"]}


builder = StateGraph(
    state_schema=OverAllState,
    input_schema=InputState,
    output_schema=OutputState,
)
builder.add_node(node_start)
builder.add_node(node_llm)
builder.add_node(node_end)

builder.add_edge(START, "node_start")
builder.add_edge("node_start", "node_llm")
builder.add_edge("node_llm", "node_end")
builder.add_edge("node_end", END)

graph = builder.compile()

result = graph.invoke({"username": "charlotte", "input": "你好"})
rprint(result)
