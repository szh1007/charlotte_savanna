import os

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import CharacterTextSplitter, RecursiveCharacterTextSplitter
from rich import print as rprint

# split_documents(list[Document]) -> list[Document]
# ↓
# ↓ 调用了
# ↓
# create_documents(list[str]) -> list[Document]
# ↓
# ↓ 调用了
# ↓
# split_text(str) -> list[str]

md_docs = TextLoader(os.path.join(os.path.dirname(__file__), "load", "sample.md"), encoding="utf-8").load()

splitter1 = CharacterTextSplitter(
    chunk_size=1000,  # 块大小
    chunk_overlap=50,  # 可重叠大小
    separator="\n\n",  # 分隔符
)
# docs1 = splitter1.split_documents(md_docs)
# rprint(docs1)

# 常用 SOTA
spliter2 = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=50,
    separators=["\n\n", "\n", " ", ""],  # 这里是默认值, 可自定义
)
docs2 = splitter1.split_documents(md_docs)
rprint(docs2)
