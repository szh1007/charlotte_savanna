"""检索器 retriever"""

import os
import warnings

import dotenv
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

warnings.filterwarnings("ignore", category=DeprecationWarning)

dotenv.load_dotenv()
os.environ["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
embedding_model = OpenAIEmbeddings(model="text-embedding-3-large")

splitter = RecursiveCharacterTextSplitter(
    chunk_size=100,
    chunk_overlap=10,
    add_start_index=True,
    length_function=len,
    separators=["。", ","],
)

""" 数据的持久化 """
docs = TextLoader(file_path="./asset/09-ai2.txt", encoding="utf-8").load()
split_docs = splitter.split_documents(docs)

faiss_db = FAISS.from_documents(documents=split_docs, embedding=embedding_model)
faiss_db.save_local("./chroma-2")

""" 数据检索 """
# # 相似度检索
# retriever = faiss_db.as_retriever(search_kwargs={"k": 3})
# response_docs1 = retriever.invoke("经济政策")
# for i, doc in enumerate(response_docs1):
#     print(f"{i + 1}: {doc.page_content[:10]}...")

# # 分数阈值检索
# retriever = faiss_db.as_retriever(
#     search_type="similarity_score_threshold",
#     search_kwargs={"score_threshold": 0.1},
# )
# response_docs2 = retriever.invoke("人工智能的应用领域")
# for i, doc in enumerate(response_docs2):
#     print(f"{i + 1}: {doc.page_content[:10]}...")

# MMR 最大边际相关性 0-1
retriever = faiss_db.as_retriever(search_type="mmr", search_kwargs={"fetch_key": 2})
response_docs3 = retriever.invoke("人工智能的应用领域")
for _i, _doc in enumerate(response_docs3):
    pass
