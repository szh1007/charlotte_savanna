import os
from collections.abc import Sequence

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import Send
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


class ProvateWorkState(MessagesState):
    content_type: str = Field(description="内容类型")
    prompt: str = Field(description="提示词")


class InputState(MessagesState):
    topic: str = Field(description="主题")


class OutputState(MessagesState):
    poem: str = Field(description="诗")
    joke: str = Field(description="笑话")
    lyrics: str = Field(description="歌词")


def work_node(state: ProvateWorkState) -> OutputState:
    response = model.invoke([HumanMessage(state["prompt"])])
    return {state["content_type"]: response.content}


def router(state: InputState) -> Sequence[Send]:
    prompt = "请生成关于{}的{}"
    en2zh = {"poem": "诗", "joke": "笑话", "lyrics": "歌词"}
    return [
        Send(
            "work_node",
            ProvateWorkState(
                content_type=content_type,
                prompt=prompt.format(state["topic"], en2zh[content_type]),
            ),
        )
        for content_type in en2zh
    ]


builder = StateGraph(
    state_schema=OverAllState,
    input_schema=InputState,
    output_schema=OutputState,
)
builder.add_node(work_node)


builder.add_conditional_edges(
    START,
    router,
    path_map={
        "worker": "work_node",
    },
)

builder.add_edge("work_node", END)

graph = builder.compile()
rprint(graph.get_graph().draw_mermaid())

result = graph.invoke({"topic": "猫"})
rprint(result)
