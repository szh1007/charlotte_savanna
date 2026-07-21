import dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain.chat_models import init_chat_model
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})
# rprint(model)

agent = create_agent(
    model=model,
    middleware=[
        SummarizationMiddleware(  # 历史消息摘要中间件
            model=model,
            trigger=[  # 摘要触发条件
                ("tokens", 100),  # token 数量
                ("messages", 5),  # 历史消息数量
                ("fraction", 0.5),  # 上下文长度占比, 要配合 profile - max_input_tokens 使用
            ],
            keep=("messages", 2),  # 摘要后, 保留的原始消息数量
            summary_prompt="用中文对历史消息进行摘要, 消息列表如下: {messages}",
        ),
    ],
)

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

response = agent.invoke({"messages": messages})
rprint(response)
