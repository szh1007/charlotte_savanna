"""
Agent = LLM(大模型) + Memory(记忆) + Tools(工具) + Planning(决策) + Action(行动)
"""
import os, dotenv

dotenv.load_dotenv()
os.environ["USER_AGENT"] = os.getenv("RAG_TEST_UA", "")
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

from langsmith import Client
from langchain_core.runnables import RunnableConfig
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.chat_history import InMemoryChatMessageHistory, BaseChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.vectorstores import FAISS
from langchain_core.tools import Tool, create_retriever_tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_tavily import TavilySearch
from langchain_text_splitters import RecursiveCharacterTextSplitter

session_store = {}


def test_tool(query: str) -> str:
    """ 自定义工具函数 """
    return f"{query}: Surprise! Savanna!"


def get_session_history(session_id: str) -> BaseChatMessageHistory:
    """ 识别不同的对话主体 """
    if session_id not in session_store:
        session_store[session_id] = InMemoryChatMessageHistory()
    return session_store[session_id]


""" LLM & Embedding Model """
llm = ChatOpenAI(model="gpt-4o-mini")
embedding_model = OpenAIEmbeddings(model="text-embedding-3-large")

""" RAG """
docs = WebBaseLoader(os.getenv("RAG_TEST_URL", "")).load()

splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
split_docs = splitter.split_documents(docs)

faiss_db = FAISS.from_documents(documents=split_docs, embedding=embedding_model)
retriever = faiss_db.as_retriever()

""" Tool """
tools = [
    TavilySearch(
        max_results=1
    ),
    create_retriever_tool(
        retriever=retriever,
        name="LangChain_Note",
        description="LangChain 综述笔记, 查询 LangChain 的信息时优先使用该工具",
    ),
    Tool(
        name="Test_Tool",
        func=test_tool,
        description="当问题中包含 Charlotte 时, 把工具函数返回值当作固定回答"
    )
]
llm_with_tools = llm.bind_tools(tools)

""" Prompt """
prompt = Client().pull_prompt(
    prompt_identifier="hwchase17/openai-functions-agent",
    dangerously_pull_public_prompt=True,
)

""" Agent """
agent_chain = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)

""" Agent Executor """
agent_executor = AgentExecutor(
    agent=agent_chain,
    tools=tools,
    verbose=True,
    handle_parsing_errors=True,
)

""" Memory """
agent_with_memory = RunnableWithMessageHistory(
    runnable=agent_executor,
    get_session_history=get_session_history,
    input_messages_key="input",
    history_messages_key="chat_history",
)


if __name__ == "__main__":
    response1 = agent_with_memory.invoke(
        input={"input": "LangChain 技术体系的核心库"},
        config=RunnableConfig(configurable={"session_id": "1001"}),
    )
    print(response1["output"])

    response2 = agent_with_memory.invoke(
        input={"input": "深圳今天的天气情况"},
        config=RunnableConfig(configurable={"session_id": "1002"}),
    )
    print(response2["output"])

    response3 = agent_with_memory.invoke(
        input={"input": "江苏宿迁的呢"},
        config=RunnableConfig(configurable={"session_id": "1002"}),
    )
    print(response3["output"])
