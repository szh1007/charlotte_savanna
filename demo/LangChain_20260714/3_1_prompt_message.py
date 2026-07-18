import dotenv
from langchain.chat_models import init_chat_model
from langchain.messages import AIMessage, HumanMessage, SystemMessage
from rich import print as rprint

dotenv.load_dotenv()

llm = init_chat_model("deepseek:deepseek-v4-pro")

""" Message """

# messages = [
#     {"role": "system", "content": "你是信息提取器, 提取所有对话历史中的用户输入内容, 并带上name字段, 返回JSON格式"},
#     {"role": "user", "content": "Hello, I am Charlotte", "name": "charlotte"},
#     {"role": "assistant", "content": "Hi, Charlotte", "tool_calls": []},
#     {"role": "user", "content": "Hello, I am Savanna", "name": "savanna"},
#     {"role": "assistant", "content": "Hi, Savanna", "tool_calls": []},
# ]

messages = [
    SystemMessage("你是信息提取器, 提取所有对话历史中的用户输入内容, 并带上name字段, 返回JSON格式"),
    HumanMessage("Hello, I am Charlotte", name="charlotte"),
    AIMessage("Hi, Charlotte", tool_calls=[]),
    HumanMessage("Hello, I am Savanna", name="savanna"),
    AIMessage("Hi, Savanna", tool_calls=[]),
]
response = llm.invoke(messages)
rprint(response.content, "\n")

# content_blocks
# 1.输入兼容多个厂商API的多模态内容、推理过程等
# 2.输出格式化, 包括多模态内容、推理过程等
rprint(response.content_blocks)

# --- 建议以后直接使用 content_blocks ---
