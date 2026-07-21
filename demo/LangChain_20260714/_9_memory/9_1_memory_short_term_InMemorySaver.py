import dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver
from rich import print as rprint

"""
短期记忆: State (会话状态) + Checkpointer (持久化机制) + Thread ID (会话窗口表标识)
"""

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})
agent = create_agent(
    model=model,
    checkpointer=InMemorySaver(),  # *内存级记忆存储*
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
response = agent.invoke({"messages": messages1}, config=config)
rprint(response["messages"][-1].content)
rprint("-" * 50)

# 查看内存记忆
rprint(agent.get_state(config=config))
rprint("-" * 50)

# 第2轮对话
messages2 = [
    ("human", "你还记得我叫什么吗"),
]
response = agent.invoke({"messages": messages2}, config=config)
rprint(response["messages"][-1].content)
rprint("-" * 50)

# 查看内存记忆
rprint(agent.get_state(config=config))
