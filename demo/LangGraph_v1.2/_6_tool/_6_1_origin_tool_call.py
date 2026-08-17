import os
from typing import Literal

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)


@tool(parse_docstring=True)
def get_weather(location: str, time: str) -> str:
    """
    获取指定地点和时间的天气

    Args:
        location: 地点
        time: 时间

    Returns:
        天气描述
    """
    return f"{time}{location}的天气: 多云转雷阵雨"


@tool(parse_docstring=True)
def get_news(location: str) -> str:
    """
    获取指定地点的新闻

    Args:
        location: 地点

    Returns:
        新闻描述
    """
    return f"{location}最新新闻: DeepSeek-v4-flash 正式版API发布"


tools_by_name = {
    "get_weather": get_weather,
    "get_news": get_news,
}
tools = list(tools_by_name.values())

model_with_tools = model.bind_tools(tools)


class OverAllState(MessagesState):
    output: str = Field(description="模型输出")


def llm_node(state: OverAllState) -> OverAllState:
    response = model_with_tools.invoke(state["messages"])
    return {"messages": [response], "output": response.content}


def tool_node(state: OverAllState) -> OverAllState:
    tool_calls = state["messages"][-1].tool_calls
    if not tool_calls:
        return {}

    tool_messages = []
    for call in tool_calls:
        called_tool = tools_by_name[call["name"]]
        call_result = called_tool.invoke(call["args"])
        tool_message = ToolMessage(
            name=call["name"],
            content=call_result,
            tool_call_id=call["id"],
        )
        tool_messages.append(tool_message)
    return {"messages": tool_messages}


def router(state: OverAllState) -> Literal["tool", "END"]:
    if state["messages"][-1].tool_calls:
        return "tool"
    return "END"


graph = (
    StateGraph(OverAllState)
    .add_node(llm_node)
    .add_node(tool_node)
    .add_edge(START, "llm_node")
    .add_conditional_edges(
        "llm_node",
        router,
        path_map={
            "tool": "tool_node",
            "END": END,
        },
    )
    .add_edge("tool_node", "llm_node")
).compile()

rprint(graph.invoke({"messages": [HumanMessage(content="深圳今天的天气和新闻")]}))
