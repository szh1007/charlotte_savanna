import os

import dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.postgres import PostgresSaver
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

"""
短期记忆的两种方式的区别
1. 内存级记忆存储 InMemorySaver: 仅在内存中存储记忆, 重新运行进程后就会丢失记忆
2. 持久化记忆存储 PostgresSaver: 将记忆存储在 PostgreSQL, 【尽管重新运行进程, 只要会话ID相同, 就可以恢复记忆】
"""


# 连接 PostgreSQL
PG_DB_URL = f"postgresql://{os.getenv('PG_DB_USERNAME', '')}:{os.getenv('PG_DB_PASSWORD', '')}\
    @{os.getenv('PG_DB_HOST', '')}:{os.getenv('PG_DB_PORT', '')}\
        /{os.getenv('PG_DB_NAME', '')}\
            ?sslmode=disable"

with PostgresSaver.from_conn_string(PG_DB_URL) as checkpointer:
    # 启动 PostgreSQL checkpointer
    checkpointer.setup()

    agent = create_agent(
        model=model,
        checkpointer=checkpointer,
    )

    # *配置会话线程*
    config = {"configurable": {"thread_id": "1"}}

    # 第1轮对话
    messages1 = [
        ("system", "你是专业的智能AI助手"),
        ("human", "你好, 我叫Charlotte"),
        ("ai", "你好, 有什么可以帮到你"),
        ("human", "5! =?"),
        ("ai", "5! = 120"),
        ("human", "10! = ?"),
    ]
    response1 = agent.invoke({"messages": messages1}, config=config)
    rprint(response1["messages"][-1].content)
    rprint("-" * 50)

    # 第2轮对话
    messages2 = [
        ("human", "你还记得我叫什么吗"),
    ]
    response2 = agent.invoke({"messages": messages2}, config=config)
    rprint(response2["messages"][-1].content)
    rprint("-" * 50)
