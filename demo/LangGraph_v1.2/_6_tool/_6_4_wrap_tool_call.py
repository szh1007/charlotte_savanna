import json
import random
import warnings
from dataclasses import dataclass
from typing import Literal

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.prebuilt import ToolNode, ToolRuntime
from langgraph.types import Command
from loguru import logger
from pydantic import Field
from rich import print as rprint

warnings.filterwarnings("ignore", category=UserWarning)

dotenv.load_dotenv()

model = init_chat_model(
    "deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}
)


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
    if random.randint(1, 10) < 3:
        logger.info("模拟调用工具失败")
        raise ConnectionError("模拟调用工具失败")

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
    if random.randint(1, 10) < 3:
        logger.info("模拟调用工具失败")
        raise ConnectionError("模拟调用工具失败")

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


@dataclass
class TestUserContext:
    max_attempts: int


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


global_cache = {}


def wrap_tool_call(request, execute):
    tool_name = request.tool_call["name"]
    tool_args = json.dumps(request.tool_call["args"])
    tool_call_id = request.runtime.tool_call_id
    max_attempts = request.runtime.context.max_attempts

    cache_key = (tool_name, tool_args)
    cache = global_cache.get(cache_key)

    # wrap_tool_call - 重试机制
    tool_message = None
    for item in range(max_attempts):
        try:
            # wrap_tool_call - 缓存机制
            if cache:
                logger.info(f"缓存命中-{tool_name}: {cache}")
                tool_message = ToolMessage(
                    name=f"缓存命中-{tool_name}",
                    content=cache,
                    tool_call_id=tool_call_id,
                )
            else:
                # 表示1次工具调用
                tool_message = execute(request)

                logger.info(f"缓存未命中-{tool_name}, 写入缓存")

                # 该示例中工具的返回定义成了Command, 所以要从中获取到 ToolMessage
                global_cache[cache_key] = tool_message.update["messages"][0].content

            break
        except ConnectionError as e:
            logger.info(f"调用工具失败, 第{item + 1}次, 错误信息: {e}")

    if not tool_message:
        tool_message = ToolMessage(
            name="max attempts error",
            content=f"工具调用失败, 最大尝试次数: {max_attempts}",
            tool_call_id=tool_call_id,
        )

    return tool_message


graph = (
    StateGraph(state_schema=OverAllState, context_schema=TestUserContext)
    .add_node(llm_node)
    .add_node(ToolNode(tools, wrap_tool_call=wrap_tool_call))
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

rprint(
    graph.invoke(
        input={"messages": [HumanMessage("深圳今天的天气和新闻")]},
        context=TestUserContext(max_attempts=3),
    )
)

rprint(
    graph.invoke(
        input={"messages": [HumanMessage("深圳今天的天气和新闻")]},
        context=TestUserContext(max_attempts=3),
    )
)
