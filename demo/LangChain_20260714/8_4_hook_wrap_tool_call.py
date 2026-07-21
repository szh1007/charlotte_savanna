from typing import Any

import dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ToolCallRequest, wrap_tool_call
from langchain.chat_models import init_chat_model
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import BaseModel, Field
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

"""
wrap_tool_call 应用场景: 监控、重试、修改工具执行
"""

""" Tool """


class GetWeatherFields(BaseModel):
    location: str = Field(description="城市名称")
    time: str = Field(description="时间", default="今天")


@tool(
    description="获取指定城市在指定时间的天气情况",
    args_schema=GetWeatherFields,
)
def get_weather(location: str, time: str = "今天") -> str:
    return f"{time}{location}在的天气: 多云转雷阵雨"


""" 装饰器 """


@wrap_tool_call
def wrap_tool_call_middleware(request: ToolCallRequest, handler) -> ToolMessage | Command[Any]:
    """
    Args:
        request: 工具请求元数据
        handler: 调用工具的行为
    """
    request.tool_call["args"]["time"] = "今天以及未来7天内"
    return handler(request)


""" 类 """


class TestWrapToolCallMiddleware(AgentMiddleware):
    def wrap_tool_call(self, request: ToolCallRequest, handler) -> ToolMessage | Command[Any]:
        """
        Args:
            request: 工具请求元数据
            handler: 调用工具的行为
        """
        request.tool_call["args"]["time"] = "今天以及未来7天内"
        return handler(request)


agent = create_agent(
    name="agent_assistant",
    model=model,
    tools=[get_weather],
    middleware=[
        # wrap_tool_call_middleware,
        TestWrapToolCallMiddleware(),
    ],
)


messages = [
    ("system", "你是专业的智能AI助手"),
    ("human", "深圳的天气"),
]

response = agent.invoke({"messages": messages})
rprint(response)
