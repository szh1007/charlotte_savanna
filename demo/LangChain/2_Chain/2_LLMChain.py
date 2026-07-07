"""
LLMChain (will be removed in 2.0.0)
"""
import os, dotenv

from langchain_classic.chains.sequential import (
    SequentialChain,
    SimpleSequentialChain,
)
from langchain_classic.chains.llm import LLMChain
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
model = ChatOpenAI(model="gpt-4o-mini")


"""
基础 LLMChain
"""
template1 = ChatPromptTemplate.from_messages([
    ("human", "{actor}主演的所有电影的详细汇总信息"),
])
chain1 = LLMChain(llm=model, prompt=template1, verbose=True)
# print(chain1.invoke({"actor": "周星驰"}))


"""
顺序链 SimpleSequentialChain (过于局限)
    1.每个 chain 只有 1 个输入变量和 1 个输出变量
    2.变量名可以自定义
    3.最后 invoke 仅可以传输 str
"""
template2 = ChatPromptTemplate.from_messages([
    ("human", "已知电影信息: {movies}, 选出普遍好评率最高的5部"),
])
chain2 = LLMChain(llm=model, prompt=template2, verbose=True)

ss_chain = SimpleSequentialChain(
    chains=[chain1, chain2],
    verbose=True,
)
# result1 = ss_chain.invoke("周星驰")
# print(result1["output"])


"""
顺序链 SequentialChain (常用)
    1.每个 chain 有 n 个输入变量和 n 个输出变量
    2.变量名必须要【显示定义】, 才能保证灵活映射
    3.使用 input_variables 和 output_variables 控制输入输出
"""
template3 = ChatPromptTemplate.from_messages([
    ("human", "{actor1}和{actor2}分别主演的所有电影的详细汇总信息"),
])

template4 = ChatPromptTemplate.from_messages([
    ("human", "已知{actor1}和{actor2}电影信息描述: {movies}, 综合{plat1}和{plat2}的评价选出普遍好评率最高的{top}部"),
])

chain3 = LLMChain(llm=model, prompt=template3, verbose=True, output_key="movies")
chain4 = LLMChain(llm=model, prompt=template4, verbose=True, output_key="movies_top")

s_chain = SequentialChain(
    chains=[chain3, chain4],
    input_variables=["actor1", "actor2", "plat1", "plat2", "top"],
    output_variables=["movies", "movies_top"],
    verbose=True,
)
# result2 = s_chain.invoke({"actor1": "沈腾", "actor2": "马丽", "plat1": "豆瓣", "plat2": "猫眼", "top": 3})
# print(result2["movies_top"])
