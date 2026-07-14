"""嵌入模型 embedding model"""

import os
import warnings

import dotenv
from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

warnings.filterwarnings("ignore", category=DeprecationWarning)

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
embedding_model = OpenAIEmbeddings(model="text-embedding-ada-002")

splitter = RecursiveCharacterTextSplitter(
    chunk_size=100,
    chunk_overlap=10,
    add_start_index=True,
    length_function=len,
    separators=["。", ","],
)

""" 语句嵌入 & 文档嵌入 """
# text = "Nice to meet you! Charlotte."
# query = embedding_model.embed_query(text)
# print(len(query), query[:5])

# loader = CSVLoader(file_path="./asset/03-load.csv")
# texts = [doc.page_content for doc in loader.load_and_split()]
# query = embedding_model.embed_documents(texts)
# for q in query:
#     print(len(q), q[:5])

""" 数据持久化 """
docs = TextLoader(file_path="./asset/09-ai2.txt", encoding="utf-8").load()
split_docs = splitter.split_documents(docs)

# 向量数据库中不仅存储了嵌入后的向量, 也存储了数据本身, 即保存了kv映射关系
chroma_db = Chroma.from_documents(
    documents=split_docs,
    embedding=embedding_model,
    persist_directory="./chroma-1",  # 若不指定持久化目录, 则数据会存储在内存中, 并设置缓存
)

""" 数据的检索 """
# # 相似度检索
# response_docs1 = chroma_db.similarity_search(
#     query="人工智能的应用领域",
#     k=1,  # 检索的结果数量
#     filter={"source": "./asset/09-ai2.txt"}  # metadata 元数据过滤
# )
# print(response_docs1[0])

# # 向量检索
# vect = embedding_model.embed_query("人工智能的应用领域")
# response_docs2 = chroma_db.similarity_search_by_vector(embedding=vect, k=1)
# print(response_docs2[0])

# # L2距离检索
# response_docs3 = chroma_db.similarity_search_with_score(query="介绍量子力学")
# for doc, score in response_docs3:
#     print(f"{score}: {doc.page_content[:10]}...")

# # 余弦相似度
# response_docs4 = chroma_db.similarity_search_with_relevance_scores(query="介绍量子力学")
# for doc, score in response_docs4:
#     print(f"{score}: {doc.page_content[:10]}...")

# MMR 最大边际相关性 0-1
response_docs5 = chroma_db.max_marginal_relevance_search(query="介绍量子力学", lambda_mult=0.5)
for _doc in response_docs5:
    pass
