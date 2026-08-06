from typing import Literal

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Command
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)


class OverAllState(MessagesState):
    topic: str = Field(description="主题")
    content_type: str = Field(description="内容类型英文名称")
    content_type_zh: str = Field(description="内容类型中文名称")

    poem: str = Field(description="诗")
    joke: str = Field(description="笑话")


def router(
    state: OverAllState,
) -> Command[Literal["poem_node", "joke_node", "__end__"]]:
    if state["content_type"] == "poem":
        return Command(update={"content_type_zh": "诗"}, goto="poem_node")
    elif state["content_type"] == "joke":
        return Command(update={"content_type_zh": "笑话"}, goto="joke_node")
    else:
        return Command(goto="__end__")


def poem_node(state: OverAllState) -> OverAllState:
    input = HumanMessage(f"请写一首关于{state['topic']}的诗")
    return {"poem": model.invoke([input]).content}


def joke_node(state: OverAllState) -> OverAllState:
    input = HumanMessage(f"请写一个关于{state['topic']}的笑话")
    return {"joke": model.invoke([input]).content}


builder = StateGraph(state_schema=OverAllState)
builder.add_node(router)
builder.add_node(poem_node)
builder.add_node(joke_node)

builder.add_edge(START, "router")
builder.add_edge("poem_node", END)
builder.add_edge("joke_node", END)

graph = builder.compile()

result = graph.invoke({"topic": "猫", "content_type": "poem"})
rprint(result)

result = graph.invoke({"topic": "狗", "content_type": "joke"})
rprint(result)

result = graph.invoke({"topic": "狗", "content_type": "xxxx"})
rprint(result)
