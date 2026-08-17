import os
from typing import Literal

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.prebuilt import ToolNode, ToolRuntime
from langgraph.types import Command
from pydantic import Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)

"""
tool 中可以使用 ToolRuntime + Command 来更新状态
不建议在 Command 中使用 goto, 会让逻辑更加混乱, 建议分在节点流中处理
"""


@tool(parse_docstring=True)
def get_weather(location: str, time: str, runtime: ToolRuntime) -> Command:
    """
    获取指定地点和时间的天气

    Args:
        location: 地点
        time: 时间

    Returns:
        天气描述
    """
    result = f"{time}{location}的天气: 多云转雷阵雨"
    tool_message = ToolMessage(
        name="get_weather",
        content=result,
        tool_call_id=runtime.tool_call_id,
    )
    return Command(update={"result1": result, "messages": [tool_message]})


@tool(parse_docstring=True)
def get_news(location: str, runtime: ToolRuntime) -> Command:
    """
    获取指定地点的新闻

    Args:
        location: 地点

    Returns:
        新闻描述
    """
    result = f"{location}最新新闻: DeepSeek-v4-flash 正式版API发布"
    tool_message = ToolMessage(
        name="get_news",
        content=result,
        tool_call_id=runtime.tool_call_id,
    )
    return Command(update={"result2": result, "messages": [tool_message]})


tools_by_name = {
    "get_weather": get_weather,
    "get_news": get_news,
}
tools = list(tools_by_name.values())

model_with_tools = model.bind_tools(tools)


class OverAllState(MessagesState):
    result1: str = Field(description="工具结果1")
    result2: str = Field(description="工具结果2")
    output: str = Field(description="模型输出")


def llm_node(state: OverAllState) -> OverAllState:
    response = model_with_tools.invoke(state["messages"])
    return {"messages": [response], "output": response.content}


def router(state: OverAllState) -> Literal["tool", "END"]:
    if state["messages"][-1].tool_calls:
        return "tool"
    return "END"


graph = (
    StateGraph(OverAllState)
    .add_node(llm_node)
    .add_node(ToolNode(tools))  # ToolNode 节点名称默认为 "tools"
    .add_edge(START, "llm_node")
    .add_conditional_edges(
        "llm_node",
        router,
        path_map={
            "tool": "tools",
            "END": END,
        },
    )
    .add_edge("tools", "llm_node")
).compile()

rprint(graph.invoke({"messages": [HumanMessage(content="深圳今天的天气和新闻")]}))
