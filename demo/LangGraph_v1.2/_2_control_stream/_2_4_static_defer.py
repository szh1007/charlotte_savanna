import os

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from loguru import logger
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


def audit(state: OverAllState) -> OverAllState:
    logger.info(
        "任务已全部执行完毕: "
        + f"poem-{'END' if state['poem'] else 'ERROR'}, "
        + f"joke-{'END' if state['joke'] else 'ERROR'}",
    )


builder = StateGraph(state_schema=OverAllState)
builder.add_node(node1)
builder.add_node(node2)
builder.add_node(audit, defer=True)

builder.add_edge(START, "node1")
builder.add_edge(START, "node2")
builder.add_edge(START, "audit")

builder.add_edge("node1", END)
builder.add_edge("node2", END)
builder.add_edge("audit", END)

graph = builder.compile()
rprint(graph.get_graph().draw_mermaid())

result = graph.invoke({"topic": "猫"})
rprint(result)
