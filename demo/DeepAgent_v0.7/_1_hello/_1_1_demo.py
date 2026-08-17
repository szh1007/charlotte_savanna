import os

import dotenv
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from rich import print as rprint
from tavily import TavilyClient

"""
DeepAgent 3条铁律
1.问题极度开放: 已经不足以用 LangGraph 条件边解决
2.存在领域冲突: 单智能体执行的话, 不同领域之间会互相污染上下文, 降低大模型能力
    主智能体和子智能体有【不同的上下文】
3.需要多方向并行: 不同类型的任务, 并行可以提高效率
4.需要使用多类型的模型: 语言模型 / 视觉模型 / 嵌入式模型 / ...

DeepAgent 架构模式 - 层级工作流 - 主从模式
1.主agent负责任务调度和结果反馈, 子agent专注某个方向的任务
2.主agent挂了, 所有子agent就无法运作了
3.子agent之间的所有内容(上下文、运行时、记忆等)相互【隔离】
"""

dotenv.load_dotenv()

model = init_chat_model(
    model=os.getenv("DEEPSEEK_MODEL_NAME", ""),
    extra_body={"thinking": {"type": "disabled"}},
)

tavily = TavilyClient()


@tool
def network_search(query: str, max_results: int = 3) -> str:
    """
    联网搜索工具

    Args:
        query: 搜索查询
        max_results: 最大返回结果数, 默认3

    Returns:
        搜索结果
    """
    rprint(f"开始联网搜索: {query}")
    rprint("-" * 100)
    return tavily.search(query, max_results=max_results)


agent = create_deep_agent(
    model=model,
    tools=[network_search],
    system_prompt="""
        角色: 专家级的研究员
        边界: 你有权使用联网搜索工具收集信息
        功能: 深入研究并撰写一份精美的研究报告
    """,
)

# result = agent.invoke({"messages": [("human", "米其林餐厅评级的来源")]})
# rprint(result["messages"][-1].content)

for chunk in agent.stream({"messages": [("human", "米其林餐厅评级的来源")]}):
    # model -- 分析 --> tool_calls / subagent / output -> AIMessage
    # tools -- 执行 --> ToolMessage
    if chunk.get("model"):
        message = chunk["model"]["messages"][-1]
        tool_calls = message.tool_calls

        rprint(message.content) if message.content else ""
        rprint(tool_calls) if tool_calls else ""
        rprint("-" * 100)
