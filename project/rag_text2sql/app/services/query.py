import json

from langchain_openai import OpenAIEmbeddings

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.repositories.es.value import ValueEsRepository
from app.repositories.mysql.dw import DwMysqlRepository
from app.repositories.mysql.meta import MetaMysqlRepository
from app.repositories.qdrant.column import ColumnQdrantRepository
from app.repositories.qdrant.metric import MetricQdrantRepository


class QueryService:
    """查询服务工具类"""

    def __init__(
        self,
        dw_mysql_repository: DwMysqlRepository,
        meta_mysql_repository: MetaMysqlRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueEsRepository,
        embeddings: OpenAIEmbeddings,
    ):
        self.dw_mysql_repository = dw_mysql_repository
        self.meta_mysql_repository = meta_mysql_repository
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository
        self.embeddings = embeddings

    # 智能体查询服务
    async def query_sse(self, query: str):
        # 状态
        state = DataAgentState(query=query)

        # 上下文
        context = DataAgentContext(
            dw_mysql_repository=self.dw_mysql_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            column_qdrant_repository=self.column_qdrant_repository,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            embeddings=self.embeddings,
        )

        try:
            # 执行
            async for chunk in graph.astream(
                input=state,
                context=context,
                stream_mode="custom",
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)} \n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False, default=str)} \n\n"  # noqa: E501
