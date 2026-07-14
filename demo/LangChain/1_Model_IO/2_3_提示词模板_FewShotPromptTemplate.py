"""提示词模板 FewShotPromptTemplate"""

import os
import warnings

import dotenv
from langchain_community.vectorstores import FAISS
from langchain_core.example_selectors import SemanticSimilarityExampleSelector
from langchain_core.prompts import (
    ChatPromptTemplate,
    FewShotChatMessagePromptTemplate,
    FewShotPromptTemplate,
    PromptTemplate,
)
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

warnings.filterwarnings("ignore", category=DeprecationWarning)

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
model = ChatOpenAI(model="gpt-4o-mini")

"""
准备样本实例
"""
examples = [
    {"input": "7217", "output": "0"},
    {"input": "7031", "output": "1"},
    {"input": "9220", "output": "2"},
    {"input": "4396", "output": "3"},
    {"input": "1894", "output": "4"},
    {"input": "4588", "output": "5"},
    {"input": "6666", "output": "4"},
    {"input": "1010", "output": "2"},
    {"input": "9008", "output": "5"},
    {"input": "2222", "output": "0"},
    {"input": "null1", "output": "null"},
    {"input": "null2", "output": "null"},
    {"input": "null3", "output": "null"},
    {"input": "null4", "output": "null"},
    {"input": "null5", "output": "null"},
]

"""
准备示例选择器 Example selectors
"""
selector = SemanticSimilarityExampleSelector.from_examples(
    examples=examples,  # 示例样本
    embeddings=OpenAIEmbeddings(),  # 编码模型
    vectorstore_cls=FAISS,  # 示例选择策略
    k=10,  # 筛选出的示例数量
)

"""
一、FewShotPromptTemplate (+ PromptTemplate)
"""
base_pt = PromptTemplate.from_template("{input} => {output}")

fspt = FewShotPromptTemplate(
    example_prompt=base_pt,
    # examples=examples,
    example_selector=selector,  # examples / example_selector 二选一
    prefix="已知以下条件",
    suffix="找规律 {question} => ?",
    input_variables=["question"],
)

p1 = fspt.invoke({"question": "8888"})

# print(p1.to_string(), "\n")
# print(model.invoke(p1).content)

"""
二、FewShotChatMessagePromptTemplate (+ ChatPromptTemplate)
"""
base_cpt = ChatPromptTemplate.from_messages([("human", "{input} => ?"), ("ai", "{output}")])

fscmpt_cpt = ChatPromptTemplate.from_messages(
    [
        ("system", "你是一个数学专家, 擅长找规律"),
        FewShotChatMessagePromptTemplate(
            example_prompt=base_cpt,
            # examples=examples,
            example_selector=selector,  # examples / example_selector 二选一
        ),
        ("human", "{question}"),
    ]
)

p2 = fscmpt_cpt.invoke({"question": "找规律 8888 => ?"})
