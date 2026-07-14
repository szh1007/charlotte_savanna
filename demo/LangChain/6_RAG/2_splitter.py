"""文档拆分器 splitter"""

import os
import warnings

import dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_experimental.text_splitter import SemanticChunker
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import (
    CharacterTextSplitter,
    RecursiveCharacterTextSplitter,
    TokenTextSplitter,
)

warnings.filterwarnings("ignore", category=DeprecationWarning)

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")

test_str = """
LangChain 是一个用于构建基于大语言模型应用的开发框架。

旨在帮助开发者更高效地集成、管理和增强大语言模型的能力,构建端到端的应用程序。
它提供了一套模块化工具和接口,支持从简单的文本生成到复杂的多步骤推理任务。
"""

""" 字符切分器 CharacterTextSplitter """
splitter1 = CharacterTextSplitter(
    chunk_size=50,  # 分块大小
    chunk_overlap=0,  # 块之间的重叠大小
    separator="。",  # 优先使用该分隔符切分, 为空时表示禁用分隔符优先
)

# texts1 = splitter1.split_text(test_str)
# for i, chunk in enumerate(texts1):
#     print(f"[{i + 1}]: {len(chunk)}")
#     print(chunk)
#     print("-" * 50)

""" 递归字符拆分器 RecursiveCharacterTextSplitter """
splitter2 = RecursiveCharacterTextSplitter(
    chunk_size=30,
    chunk_overlap=0,
    add_start_index=True,
    # separator 默认 ["\n\n", "\n", " ", ""]
)

# texts2 = splitter2.create_documents([test_str])
# for i, chunk in enumerate(texts2):
#     print(chunk)
#     print("-" * 50)


with open("./asset/08-ai1.txt", encoding="utf-8") as f:
    txt_str = f.read()

splitter3 = RecursiveCharacterTextSplitter(
    chunk_size=50,
    chunk_overlap=20,
    add_start_index=True,
    length_function=len,
    separators=["。", ","],
)

# texts3 = splitter3.create_documents([txt_str])
# for i, chunk in enumerate(texts3):
#     print(chunk.page_content)
#     print("-" * 50)

PDF = PyPDFLoader("./asset/02-load.pdf").load()
# docs = splitter3.split_documents(PDF)
# for i, chunk in enumerate(docs):
#     print(chunk.page_content)
#     print("-" * 50)

""" token拆分器 TokenTextSplitter """
splitter4 = TokenTextSplitter(
    chunk_size=50,
    chunk_overlap=0,
    encoding_name="cl100k_base",
)

# texts4 = splitter4.split_text(test_str)
# for i, chunk in enumerate(texts4):
#     print(f"[{i + 1}]: {len(chunk)}")
#     print(chunk)
#     print("-" * 50)


""" 语义拆分器 SemanticChunker """
embed = OpenAIEmbeddings(model="text-embedding-3-large")
splitter5 = SemanticChunker(
    embeddings=embed,
    breakpoint_threshold_type="percentile",
    breakpoint_threshold_amount=65.0,
)
with open("./asset/09-ai2.txt", encoding="utf-8") as f:
    ai_str = f.read()

texts5 = splitter5.create_documents([ai_str])
# for i, chunk in enumerate(texts5):
#     print(chunk)
#     print("-" * 50)
# print(len(texts5))
