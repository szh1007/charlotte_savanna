import os, dotenv

from langchain_classic.agents import AgentType, initialize_agent
from langchain_core.tools import Tool
from langchain_experimental.utilities import PythonREPL
from langchain_tavily import TavilySearch
from langchain_openai import ChatOpenAI


def test_tool(query: str) -> str:
    """ 自定义工具函数 """
    return f"{query}: Surprise! Savanna!"


""" LLM """
dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
llm = ChatOpenAI(model="gpt-4o-mini")

""" Tools """
tools = [
    TavilySearch(  # TavilySearch 继承自 BaseTool, 可直接使用 (也可以用 Tool 处理)
        max_results=3,
    ),
    Tool(  # PythonREPL 继承自 BaseModel, 需要用 Tool 处理后才能使用
        name="PythonREPL",
        func=PythonREPL().run,
        description="用于各种数学计算, 比如计算绝对值、百分比等",  # 通过修改, 以达到让模型能识别并使用的程度
    ),
    Tool(  # 自定义工具
        name="Test Tool",
        func=test_tool,
        description="当问题中包含 Charlotte 时, 直接调用并返回结果"
    )
]

""" Agent """
agent = AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION

""" Agent Executor """
agent_executor = initialize_agent(
    llm=llm,
    tools=tools,
    agent=agent,
    verbose=True,
)

# result = agent_executor.invoke("比亚迪2026年1月的股价是多少, 相比2025年1月提升/下降了几个百分比")
result = agent_executor.invoke("charlotte 的女朋友是谁")
print(result["output"])
