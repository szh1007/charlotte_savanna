import os

import dotenv
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from rich import print as rprint
from tavily import TavilyClient

"""
多智能体配置

1.CompiledSubAgent - 不常用、强耦合 - 主要用于兼容 LangChain / LangGraph
2.dict - 主流推荐
    name
    description
    system_prompt
    mode - 默认不配置, 则使用主agent的同型号模型
    tools
"""

dotenv.load_dotenv()

tavily = TavilyClient()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)


@tool
def search_weather(query: str, max_results: int = 3) -> str:
    """
    联网搜索天气的工具

    Args:
        query: 查询语句
        max_results: 最大返回结果数, 默认3

    Returns:
        天气搜索结果
    """
    rprint(f"开始联网搜索天气: {query}")
    rprint("-" * 100)
    return tavily.search(query, max_results=max_results)


math_sub_agent = {
    "name": "高级数学计算助手",
    "description": "能够计算非常复杂的数学表达式, 包含高等数学等专业性极强的任务",
    "system_prompt": "你是一个专业的数学计算助手, 能够计算非常复杂的数学表达式",
    "tools": [],
}


translate_sub_agent = {
    "name": "翻译助手",
    "description": "能够根据用户需求翻译文本",
    "system_prompt": "你是专业的翻译助手, 可以根据用户需求使用文本翻译",
    "tools": [],
}

# # CompiledSubAgent 集成 LangChain
# # 集成 LangGraph 同理, runnable=graph 即可
# langchain_agent = create_agent(
#     model=model,
#     tools=[search_weather],
# )
# weather_sub_agent = CompiledSubAgent(
#     name="天气查询助手",
#     description="能够根据用户需求查询天气的详细信息",
#     runnable=langchain_agent,
# )

weather_sub_agent = {
    "name": "天气查询助手",
    "description": "能够根据用户需求查询天气的详细信息",
    "system_prompt": "你是专业的天气查询助手, 可以根据用户需求使用工具查询天气",
    "tools": [search_weather],
}


agent = create_deep_agent(
    model=model,
    subagents=[math_sub_agent, weather_sub_agent, translate_sub_agent],
    system_prompt="""
        角色: 综合助手
        功能:
            math_sub_agent - 数学计算
            weather_sub_agent - 天气查询
            translate_sub_agent - 文本翻译
        边界: 作为主智能体, 只负责分析和任务分配, 不执行任务, 任务交给子智能体
    """,
)


if __name__ == "__main__":
    query = "查一下深圳2026年8月9日的天气, \
        并算一下2026年8月9日之前7天的平均最高温, \
            最终将结果翻译成英文展示"

    for chunk in agent.stream({"messages": [("human", query)]}):
        if chunk.get("model"):
            message = chunk["model"]["messages"][-1]
            rprint(message.content) if message.content else ""
            rprint("-" * 100)
        else:
            rprint(chunk)
