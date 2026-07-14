"""提示词模板 PromptTemplate"""

import os

import dotenv
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
model = ChatOpenAI(model="gpt-4o-mini")

"""
一、实例化
"""
# 方式1
template1 = PromptTemplate(
    template="你是一个{role}, 你的名字是{name}",
    input_variables=["role", "name"],
)

# 方式2 (推荐)
template2 = PromptTemplate.from_template(
    template="你是一个{role}, 你的名字是{name}",
)

# print(template1)
# print(template2)
# print("-" * 100)

"""
二、默认值
"""
# 方式1
template3 = PromptTemplate.from_template(
    template="你是一个{role}, 你的名字是{name}",
    partial_variables={"role": "AI专家"},
)

# 方式2
template4 = PromptTemplate.from_template(template="你是一个{role}, 你的名字是{name}").partial(role="AI专家")

# print(template3)
# print(template4)
# print("-" * 100)

"""
三、赋值
    invoke()    传入 dict     返回 StringPromptValue
    format()    传入 kwargs   返回 str
"""
# 方式1 (推荐)
prompt2 = template4.invoke({"name": "小智"})

# 方式2
prompt1 = template4.format(name="小智")

# print(type(prompt1), prompt1)
# print(type(prompt2), prompt2)
# print("-" * 100)

# # 结合LLM
# response = model.invoke(prompt2)
# print(response.content)
