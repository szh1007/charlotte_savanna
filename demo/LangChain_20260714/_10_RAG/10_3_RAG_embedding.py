import os

import dotenv
from langchain.embeddings import init_embeddings
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rich import print as rprint

dotenv.load_dotenv()

embedding_model = init_embeddings(
    "openai:text-embedding-3-large",
    api_key=os.getenv("OPENAI_API_KEY", ""),
    base_url=os.getenv("OPENAI_BASE_URL", ""),
)

# loader
src_docs = TextLoader(os.path.join(os.path.dirname(__file__), "load", "sample.md"), encoding="utf-8").load()

# splitter
docs = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=50,
).split_documents(src_docs)
texts = [doc.page_content for doc in docs]

# embedding
embeddings = embedding_model.embed_documents(texts)
for k, v in zip(texts, embeddings, strict=False):
    rprint(f"{k[:10]}...: {v[:5]}...")
