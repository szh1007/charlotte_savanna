from fastapi.params import Depends
from langchain_openai import OpenAIEmbeddings
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.embedding import embedding_client
from app.clients.es import es_client
from app.clients.mysql import dw_client, meta_client
from app.clients.qdrant import qdrant_client
from app.repositories.es.value import ValueEsRepository
from app.repositories.mysql.dw import DwMysqlRepository
from app.repositories.mysql.meta import MetaMysqlRepository
from app.repositories.qdrant.column import ColumnQdrantRepository
from app.repositories.qdrant.metric import MetricQdrantRepository
from app.services.query import QueryService

"""
依赖注入组件

公用共享逻辑: 数据库连接, 安全校验, 身份验证, 角色要求...
"""


async def get_dw_session():
    async with dw_client.session() as session:
        yield session


async def get_meta_session():
    async with meta_client.session() as session:
        yield session


async def get_dw_mysql_repository(session: AsyncSession = Depends(get_dw_session)):
    return DwMysqlRepository(session)


async def get_meta_mysql_repository(session: AsyncSession = Depends(get_meta_session)):
    return MetaMysqlRepository(session)


async def get_column_qdrant_repository():
    return ColumnQdrantRepository(qdrant_client.client)


async def get_metric_qdrant_repository():
    return MetricQdrantRepository(qdrant_client.client)


async def get_value_es_repository():
    return ValueEsRepository(es_client.client)


async def get_embeddings():
    return embedding_client.embeddings


# 返回 service 依赖项的函数
async def get_query_service(
    dw_mysql_repository: DwMysqlRepository = Depends(
        get_dw_mysql_repository,
    ),
    meta_mysql_repository: MetaMysqlRepository = Depends(
        get_meta_mysql_repository,
    ),
    column_qdrant_repository: ColumnQdrantRepository = Depends(
        get_column_qdrant_repository,
    ),
    metric_qdrant_repository: MetricQdrantRepository = Depends(
        get_metric_qdrant_repository,
    ),
    value_es_repository: ValueEsRepository = Depends(
        get_value_es_repository,
    ),
    embeddings: OpenAIEmbeddings = Depends(
        get_embeddings,
    ),
):
    return QueryService(
        dw_mysql_repository=dw_mysql_repository,
        meta_mysql_repository=meta_mysql_repository,
        column_qdrant_repository=column_qdrant_repository,
        metric_qdrant_repository=metric_qdrant_repository,
        value_es_repository=value_es_repository,
        embeddings=embeddings,
    )
