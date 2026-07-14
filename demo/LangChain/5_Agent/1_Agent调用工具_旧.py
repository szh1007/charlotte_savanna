import os

import dotenv
from langchain_classic.agents import AgentType, initialize_agent
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch


def test_tool(query: str) -> str:
    """自定义工具函数"""
    return f"{query}: Surprise! Savanna!"


""" LLM """
dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
llm = ChatOpenAI(model="gpt-4o-mini")

""" Tools """
tools = [
    TavilySearch(  # TavilySearch 继承自 BaseTool, 可直接使用 (也可以用 Tool 包装处理)
        max_results=3,
    ),
    Tool(  # 自定义工具
        name="Test_Tool",
        func=test_tool,
        description="当问题中包含 Charlotte 时, 直接调用并返回结果",  # 通过修改, 以达到让模型能识别并使用的程度
    ),
]

""" Agent """
agent = AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION

""" Agent Executor """
agent_executor = initialize_agent(
    llm=llm,
    tools=tools,
    agent=agent,
    verbose=True,
    handle_parsing_errors=True,
)

# result = agent_executor.invoke("charlotte 的女朋友是谁")
result = agent_executor.invoke("深圳今天的天气情况")
