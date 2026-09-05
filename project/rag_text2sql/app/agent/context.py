from typing import TypedDict

from langchain_openai import OpenAIEmbeddings

from app.repositories.qdrant.column import ColumnQdrantRepository
from app.repositories.qdrant.metric import MetricQdrantRepository


class DataAgentContext(TypedDict):
    embeddings: OpenAIEmbeddings

    column_qdrant_repository: ColumnQdrantRepository
    metric_qdrant_repository: MetricQdrantRepository
