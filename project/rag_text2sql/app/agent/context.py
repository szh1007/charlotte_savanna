from typing import TypedDict

from langchain_openai import OpenAIEmbeddings

from app.repositories.qdrant.column import ColumnQdrantRepository


class DataAgentContext(TypedDict):
    embeddings: OpenAIEmbeddings

    column_qdrant_repository: ColumnQdrantRepository
