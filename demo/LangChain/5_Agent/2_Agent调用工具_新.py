import os

import dotenv
from langchain_classic.agents import (
    AgentExecutor,
    create_react_agent,
    create_tool_calling_agent,
)
from langchain_classic.memory import ConversationBufferMemory
from langchain_core.prompts import (
    ChatPromptTemplate,
    MessagesPlaceholder,
)
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
from langsmith import Client


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

""" Prompt """
prompt1 = ChatPromptTemplate.from_messages(
    [
        ("system", "你是智能AI助手, 根据用户的提问, 必要时调用 tavily_search 工具进行互联网检索"),
        MessagesPlaceholder("chat_history"),
        MessagesPlaceholder("agent_scratchpad"),  # *重要!!必须要有!!!*
        ("human", "{input}"),
    ]
)

prompt2 = Client().pull_prompt(
    prompt_identifier="hwchase17/react",
    dangerously_pull_public_prompt=True,
)
prompt3 = Client().pull_prompt(
    prompt_identifier="hwchase17/react-chat",
    dangerously_pull_public_prompt=True,
)

""" Memory """
memory = ConversationBufferMemory(
    return_messages=True,
    memory_key="chat_history",
)

""" Agent """
# FUNCTION_CALL 模式: 推荐 ChatPromptTemplate
agent1 = create_tool_calling_agent(llm=llm, prompt=prompt1, tools=tools)

# ReAct 模式: 推荐 PromptTemplate (hub 在线模板)
agent2 = create_react_agent(llm=llm, prompt=prompt2, tools=tools)
agent3 = create_react_agent(llm=llm, prompt=prompt3, tools=tools)

""" Agent Executor """
agent_executor = AgentExecutor(
    agent=agent3,
    tools=tools,
    memory=memory,
    verbose=True,
    handle_parsing_errors=True,
)

# result = agent_executor.invoke({"input": "charlotte 的女朋友是谁"})
# print(result["output"])

result1 = agent_executor.invoke({"input": "深圳今天的天气情况"})
result2 = agent_executor.invoke({"input": "江苏宿迁的呢"})
