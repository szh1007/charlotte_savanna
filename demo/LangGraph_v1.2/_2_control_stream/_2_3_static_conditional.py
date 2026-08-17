import os
from typing import Literal

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState, Sequence
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)


class OverAllState(MessagesState):
    topic: str = Field(description="主题")
    poem: str = Field(description="诗")
    joke: str = Field(description="笑话")
    lyrics: str = Field(description="歌词")


def node1(state: OverAllState) -> OverAllState:
    input = HumanMessage(f"请写一首关于{state['topic']}的诗")
    response = model.invoke([input])
    return {"messages": [input, response], "poem": response.content}


def node2(state: OverAllState) -> OverAllState:
    input = HumanMessage(f"请写一个关于{state['topic']}的笑话")
    response = model.invoke([input])
    return {"messages": [input, response], "joke": response.content}


def node3(state: OverAllState) -> OverAllState:
    input = HumanMessage(f"请写一段关于{state['topic']}的歌词")
    response = model.invoke([input])
    return {"messages": [input, response], "lyrics": response.content}


"""条件路由"""


def test_route(state: OverAllState) -> Sequence[Literal["poem", "joke", "lyrics"]]:
    if state["topic"] in ["猫", "狗"]:
        return ["poem", "lyrics"]
    else:
        return ["joke", "lyrics"]


builder = StateGraph(state_schema=OverAllState)
builder.add_node(node1)
builder.add_node(node2)
builder.add_node(node3)

# 定义 path_map -> 方便 draw_mermaid
builder.add_conditional_edges(
    START, test_route, path_map={"poem": "node1", "joke": "node2", "lyrics": "node3"}
)

builder.add_edge("node1", END)
builder.add_edge("node2", END)
builder.add_edge("node3", END)

graph = builder.compile()
rprint(graph.get_graph().draw_mermaid())

result = graph.invoke({"topic": "猫"})
rprint(result)

result = graph.invoke({"topic": "仓鼠"})
rprint(result)
