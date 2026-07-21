import dotenv
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import SummarizationMiddleware, after_model, before_model
from langchain.chat_models import init_chat_model
from langchain.messages import RemoveMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from rich import print as rprint
from streamlit.runtime import Runtime

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})


@before_model
def trim_messages(state: AgentState, runtime: Runtime):
    """消息裁剪 - before_model"""
    messages = state["messages"]
    if len(messages) <= 3:
        return

    first_message = messages[0]
    recent_messages = messages[-3:] if len(messages) % 2 == 0 else messages[-4:]
    new_messages = [first_message, *recent_messages]

    return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *new_messages]}


@after_model
def delete_messages(state: AgentState, runtime: Runtime):
    """消息删除 - after_model"""
    messages = state["messages"]
    if len(messages) > 5:
        to_delete = len(messages) - 5

        # RemoveMessage 不会真的在内存中删除消息, 而是追加"墓碑标记"
        # 下一次对话时, 框架会将有"墓碑标记"的消息对外隐藏
        return {"messages": [RemoveMessage(id=m.id) for m in messages[:to_delete]]}
    return


agent = create_agent(
    model=model,
    checkpointer=InMemorySaver(),
    middleware=[
        trim_messages,
        delete_messages,
        SummarizationMiddleware(  # 消息摘要 (7_1)
            model="deepseek:deepseek-v4-flash",
            trigger=[
                ("tokens", 100),
                ("messages", 5),
                ("fraction", 0.5),
            ],
            keep=("messages", 2),
            summary_prompt="用中文对历史消息进行摘要, 消息列表如下: {messages}",
        ),
    ],
)
config = {"configurable": {"thread_id": "1"}}

messages = [
    ("system", "你是专业的智能AI助手"),
    ("human", "你好, 我叫Charlotte"),
    ("ai", "你好, 有什么可以帮到你"),
    ("human", "5! =?"),
    ("ai", "5! = 120"),
    ("human", "10! = ?"),
    ("ai", "10! = 36288000"),
    ("human", "你还记得我叫什么吗"),
]

response = agent.invoke({"messages": messages}, config=config)
rprint(response)
