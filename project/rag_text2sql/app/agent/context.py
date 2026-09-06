from typing import TypedDict

from langchain_openai import OpenAIEmbeddings

from app.repositories.es.value import ValueEsRepository
from app.repositories.mysql.dw import DwMysqlRepository
from app.repositories.mysql.meta import MetaMysqlRepository
from app.repositories.qdrant.column import ColumnQdrantRepository
from app.repositories.qdrant.metric import MetricQdrantRepository


class DataAgentContext(TypedDict):
    embeddings: OpenAIEmbeddings

    dw_mysql_repository: DwMysqlRepository
    meta_mysql_repository: MetaMysqlRepository
    column_qdrant_repository: ColumnQdrantRepository
    metric_qdrant_repository: MetricQdrantRepository
    value_es_repository: ValueEsRepository
