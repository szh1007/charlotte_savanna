"""提示词模板 ChatPromptTemplate"""

import os

import dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
model = ChatOpenAI(model="gpt-4o-mini")

"""
一、实例化
    参数本质上都是消息列表 (Messages), 只是列表中的"消息"有不同的形式
"""
# 方式1 构造方法 - 常用 tuple
template1 = ChatPromptTemplate(
    [
        ("system", "你是一个AI助手, 你的名字叫{name}"),
        ("human", "我的问题是{question}"),
    ]
)

# 方式2 from_messages - 常用 tuple (还可以是: str、dict、Message、ChatPromptTemplate、MessagePromptTemplate)
template2 = ChatPromptTemplate.from_messages(
    [
        ("system", "你是一个AI助手, 你的名字叫{name}"),
        ("human", "我的问题是{question}"),
    ]
)

"""
二、赋值
    invoke()            传入 dict     返回 ChatPromptValue (推荐)
    format()            传入 kwargs   返回 str
    format_messages()   传入 kwargs   返回 Messages
    format_prompt()     传入 kwargs   返回 ChatPromptValue

    SOTA = invoke() + to_string() + to_messages()
"""
prompt1 = template1.invoke({"name": "小智", "question": "介绍一下LLM"})
prompt2 = template1.format(name="小智", question="介绍一下LLM")
prompt3 = template1.format_messages(name="小智", question="介绍一下LLM")
prompt4 = template1.format_prompt(name="小智", question="介绍一下LLM")

# print(type(prompt1), prompt1, sep="\n")
# print(type(prompt2), prompt2, sep="\n")
# print(type(prompt3), prompt3, sep="\n")
# print(type(prompt4), prompt4, sep="\n")
# print("-" * 100)

# # ChatPromptValue -> str / Messages
# print(prompt1.to_string())
# print(prompt1.to_messages())
# print("-" * 100)

# # 结合LLM
# response = model.invoke(prompt1)
# print(response.content)
# print("-" * 100)

"""
三、插入消息列表 MessagesPlaceholder
    当模板中的消息类型和数量不确定时, 可以用于临时占位
"""
template3 = ChatPromptTemplate.from_messages(
    [("system", "你是一个AI助手, 你的名字叫{name}"), MessagesPlaceholder("history"), ("human", "{question}")]
)

prompt5 = template3.invoke(
    {
        "name": "小智",
        "history": [
            AIMessage("有什么可以帮到你"),
            HumanMessage("1 + 2 + ... + 100 = ?"),
            AIMessage("5050"),
        ],
        "question": "我刚才问了什么问题",
    }
)

# response = model.invoke(prompt5)
# print(response.content)
