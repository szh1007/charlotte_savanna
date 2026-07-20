import dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import PIIMiddleware
from langchain.chat_models import init_chat_model
from rich import print as rprint

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

agent = create_agent(
    model=model,
    middleware=[  # 敏感信息保护
        PIIMiddleware("email", strategy="redact", apply_to_input=True),  # 通常在模型调用前保护即可
        PIIMiddleware("credit_card", strategy="mask", apply_to_input=True),
        PIIMiddleware("url", strategy="hash", apply_to_input=True),
        PIIMiddleware("mac_address", strategy="mask", apply_to_input=True),
        # PIIMiddleware("ip", strategy="block", apply_to_input=True),
    ],
)

messages = [
    ("system", "你是专业的智能AI助手"),
    (
        "human",
        """检查以下信息
     邮箱: 1016153653@qq.com
     信用卡号: 6222265426516514
     网站: https://www.baidu.com
     MAC地址: 0:0a:95:9d:68:16
     IP地址: 192.168.1.1
    """,
    ),
]

try:
    response = agent.invoke({"messages": messages})
    rprint(response["messages"][-1].content)
except Exception as e:
    rprint(e)
