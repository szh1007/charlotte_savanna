from random import randint
from typing import Literal

import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from pydantic import Field
from rich import print as rprint

"""
静态循环 - add_conditional_edges
"""

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

""" 工具 """


@tool(parse_docstring=True)
def get_weather(location: str, time: str = "今天") -> str:
    """
    获取指定城市在指定时间的天气情况

    Args:
        location (str): 城市名称
        time (str, optional): 时间. Defaults to "今天".
    """
    return f"{time}{location}的天气: 多云转雷阵雨"


tavily_search = TavilySearch(max_results=3)

tools = [get_weather, tavily_search]

model_with_tools = model.bind_tools(tools=tools)


""" State & Node"""


class OverAllState(MessagesState):
    """整体状态"""

    user_input: str = Field(..., description="用户输入")
    final_output: str = Field("", description="最终输出")


def input_node(state: OverAllState) -> OverAllState:
    """将用户输入添加到messages"""
    return {"messages": [HumanMessage(state["user_input"])]}


def llm_node(state: OverAllState) -> OverAllState:
    """调用模型, 将模型输出添加到messages"""
    ai_message = model_with_tools.invoke(state["messages"])
    return {"messages": [ai_message]}


def tool_node(state: OverAllState) -> OverAllState:
    messages = state["messages"]
    tool_calls = messages[-1].tool_calls

    # 模拟工具调用
    for call in tool_calls:
        if call["name"] == "get_weather":
            # 模拟调用失败, 测试循环工具调用的逻辑
            if randint(0, 9) < 6:
                messages.append(ToolMessage("模拟调用失败-get_weather", tool_call_id=call["id"]))
            else:
                messages.append(get_weather.invoke(call))

        elif call["name"] == "tavily_search":
            if randint(0, 9) < 6:
                messages.append(ToolMessage("模拟调用失败-tavily_search", tool_call_id=call["id"]))
            else:
                messages.append(tavily_search.invoke(call))
        else:
            messages.append(ToolMessage("模拟工具名称有误", tool_call_id=call["id"]))

    return {"messages": messages}


def output_node(state: OverAllState) -> OverAllState:
    return {"final_output": state["messages"][-1].content}


def router(state: OverAllState) -> Literal["tool_node", "output_node"]:
    """
    判断消息路由
    如果最有一个消息有tool_calls, 则说明需要调用工具 -> tool_node
    如果没有tool_calls, 则说明模型已经输出最终结果 -> output_node
    """
    if state["messages"][-1].tool_calls:
        return "tool_node"
    return "output_node"


""" Graph """
builder = StateGraph(state_schema=OverAllState)
builder.add_node(input_node)
builder.add_node(llm_node)
builder.add_node(tool_node)
builder.add_node(output_node)

builder.add_edge(START, "input_node")  # 输入 - 接收用户消息
builder.add_edge("input_node", "llm_node")  # Reason 思考 - 模型开始分析
builder.add_conditional_edges("llm_node", router)  # Act 行动 - 调用工具 / 输出最终结果
builder.add_edge("tool_node", "llm_node")  # 观察 - 根据工具调用结果分析下一步操作
builder.add_edge("output_node", END)  # 输出

graph = builder.compile()
rprint(graph.get_graph().draw_mermaid())

result = graph.invoke(
    {
        "user_input": "查询深圳今天的天气",
        "messages": [
            SystemMessage(
                "你是信息查询助手, 如果模型调用工具失败, 必须重新调用直到成功, 中间不允许停止"
            )
        ],
    }
)
rprint(result)
