import uuid

from langchain_openai import OpenAIEmbeddings
from Lib.pathlib import Path
from omegaconf import OmegaConf

from app.conf.meta_config import MetaConfig, MetricConfig, TableConfig
from app.core.log import logger
from app.models.es import ValueInfoEs
from app.models.mysql import (
    ColumnInfoMySQL,
    ColumnMetricMySQL,
    MetricInfoMySQL,
    TableInfoMySQL,
)
from app.models.qdrant import ColumnInfoQdrant, MetricInfoQdrant
from app.repositories.es.value import ValueEsRepository
from app.repositories.mysql.dw import DwMysqlRepository
from app.repositories.mysql.meta import MetaMysqlRepository
from app.repositories.qdrant.column import ColumnQdrantRepository
from app.repositories.qdrant.metric import MetricQdrantRepository


class MetaService:
    def __init__(
        self,
        dw_mysql_repository: DwMysqlRepository,
        meta_mysql_repository: MetaMysqlRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        embeddings: OpenAIEmbeddings,
        value_es_repository: ValueEsRepository,
    ):
        # mysql
        self.dw_mr = dw_mysql_repository
        self.meta_mr = meta_mysql_repository

        # qdrant
        self.column_qr = column_qdrant_repository
        self.metric_qr = metric_qdrant_repository

        # embeddings
        self.embeddings = embeddings

        # es
        self.value_er = value_es_repository

    async def build(self, config_path: Path):
        # 1.加载配置文件
        context = OmegaConf.load(config_path)
        schema = OmegaConf.structured(MetaConfig)
        meta_config: MetaConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))
        logger.info(f"META配置文件加载完成\n{config_path}")

        # 2.表信息的索引构建
        if meta_config.tables:
            # 2.1 保存表信息和字段信息到meta数据库
            column_infos = await self._save_table_info_to_meta_db(meta_config.tables)
            # 2.2 保存字段信息到qdrant向量数据库
            await self._save_column_info_to_qdrant(column_infos)
            # 2.3 保存字段取值到ES全文索引库
            await self._save_column_value_to_es(meta_config.tables, column_infos)

        # 3.指标信息的索引构建
        if meta_config.metrics:
            # 3.1 保存指标信息到meta数据库
            metric_infos = await self.save_metric_info_to_meta_db(meta_config.metrics)
            # 3.2 保存指标信息到qdrant向量数据库
            await self._save_metric_info_to_qdrant(metric_infos)

    async def _save_table_info_to_meta_db(
        self, tables: list[TableConfig]
    ) -> list[ColumnInfoMySQL]:
        table_infos: list[TableInfoMySQL] = []
        column_infos: list[ColumnInfoMySQL] = []

        # 1.遍历并获取表信息
        for table in tables:
            # table_info
            table_info_mysql = TableInfoMySQL(
                id=table.name,  # 唯一 + 业务相关
                name=table.name,
                role=table.role,
                description=table.description,
            )
            table_infos.append(table_info_mysql)

            # column_info - type (查询dw获取字段类型)
            column_type_dict = await self.dw_mr.get_column_types(table.name)

            # 2.遍历并获取所有表下的字段信息
            for column in table.columns:
                # column_info - examples (查询dw获取字段取值示例)
                examples = await self.dw_mr.get_column_values(table.name, column.name)

                # column_info
                column_info_mysql = ColumnInfoMySQL(
                    id=f"{table.name}.{column.name}",  # 唯一 + 业务相关
                    name=column.name,
                    type=column_type_dict[column.name],
                    role=column.role,
                    examples=examples,
                    description=column.description,
                    alias=column.alias,
                    table_id=table.name,  # table_info - id
                )
                column_infos.append(column_info_mysql)

        # 3.保存表信息和字段信息到meta数据库
        # save内部先清空旧数据再写入, 重复构建幂等
        async with self.meta_mr.session.begin():
            await self.meta_mr.save_table_infos(table_infos)
            logger.info(f"已保存表信息到meta数据库: {len(table_infos)}")

            await self.meta_mr.save_column_infos(column_infos)
            logger.info(f"已保存字段信息到meta数据库: {len(column_infos)}")

        return column_infos  # 因为设置 expire_on_commit=False, 所以没有过期可以直接返回

    def _convert_column_info_to_qdrant(self, column_info: ColumnInfoMySQL):
        return ColumnInfoQdrant(
            id=column_info.id,
            name=column_info.name,
            type=column_info.type,
            role=column_info.role,
            examples=column_info.examples,
            alias=column_info.alias,
            description=column_info.description,
            table_id=column_info.table_id,
        )

    async def _save_column_info_to_qdrant(self, column_infos: list[ColumnInfoMySQL]):
        # 1.确保存储字段向量信息的集合存在
        await self.column_qr.ensure_collection()

        points: list[dict] = []

        # 2.遍历字段信息, 对常用字段构建向量索引
        # 每个字段需要构建3次payload相同的向量索引: name, description, alias
        for column_info in column_infos:
            points.append(  # name
                {
                    "id": uuid.uuid4(),
                    "embedding_text": column_info.name,
                    "payload": self._convert_column_info_to_qdrant(column_info),
                }
            )
            points.append(  # description
                {
                    "id": uuid.uuid4(),
                    "embedding_text": column_info.description,
                    "payload": self._convert_column_info_to_qdrant(column_info),
                }
            )
            for alia in column_info.alias:  # alias
                points.append(
                    {
                        "id": uuid.uuid4(),
                        "embedding_text": alia,
                        "payload": self._convert_column_info_to_qdrant(column_info),
                    }
                )

        # 获取所有的ids, payloads, embedding_texts
        ids, payloads, embedding_texts = [], [], []
        for point in points:
            ids.append(point["id"])
            payloads.append(point["payload"])
            embedding_texts.append(point["embedding_text"])

        # embedding_texts -> embeddings
        embeddings = []
        batch_size = 10
        for i in range(0, len(embedding_texts), batch_size):
            batch_embedding_texts = embedding_texts[i : i + batch_size]
            embedding = await self.embeddings.aembed_documents(batch_embedding_texts)
            embeddings += embedding

        await self.column_qr.upsert_column(ids, embeddings, payloads)
        logger.info(f"已保存字段向量信息到qdrant数据库: {len(ids)}")

    async def _save_column_value_to_es(
        self,
        tables: list[TableConfig],
        column_infos: list[ColumnInfoMySQL],
    ):
        # 1.确保存储字段取值的索引存在
        await self.value_er.ensure_index()

        # 2.获取配置中所有字段是否索引的描述
        column2sync: dict = {}
        for table in tables:
            for column in table.columns:
                column2sync[f"{table.name}.{column.name}"] = column.sync

        # 2.遍历所有字段取值, 构建全文索引
        # 每个字段要针对已有的不同值多次构建索引, 1个字段 -> n个取值索引
        value_infos: list[ValueInfoEs] = []
        for column_info in column_infos:
            # 判断是否需要索引
            if column2sync[column_info.id]:
                # 获取当前字段的所有值
                column_values: list[str] = await self.dw_mr.get_column_values(
                    column_info.table_id, column_info.name, 100000
                )

                sub_value_infos = [
                    ValueInfoEs(
                        id=f"{column_info.id}.{column_value}",
                        value=column_value,
                        type=column_info.type,
                        column_id=column_info.id,
                        column_name=column_info.name,
                        table_id=column_info.table_id,
                        table_name=column_info.table_id,
                    )
                    for column_value in column_values
                ]
                value_infos += sub_value_infos

        await self.value_er.save_column_values(value_infos)
        logger.info(f"已保存字段取值到ES索引: {len(value_infos)}")

    async def save_metric_info_to_meta_db(
        self, metrics: list[MetricConfig]
    ) -> list[MetricInfoMySQL]:
        metric_infos: list[MetricInfoMySQL] = []
        column_metrics: list[ColumnMetricMySQL] = []

        for metric in metrics:
            # metric_info
            metric_info = MetricInfoMySQL(
                id=metric.name,
                name=metric.name,
                description=metric.description,
                relevant_columns=metric.relevant_columns,
                alias=metric.alias,
            )
            metric_infos.append(metric_info)

            # metric_info - relevant_columns (每个指标可以关联多个字段)
            for relevant_column in metric.relevant_columns:
                column_metric = ColumnMetricMySQL(
                    column_id=relevant_column,
                    metric_id=metric.name,
                )
                column_metrics.append(column_metric)

        async with self.meta_mr.session.begin():
            await self.meta_mr.save_metric_infos(metric_infos)
            logger.info(f"已保存指标信息到meta数据库: {len(metric_infos)}")

            await self.meta_mr.save_column_metrics(column_metrics)
            logger.info(f"已保存指标字段关联信息到meta数据库: {len(column_metrics)}")

        return metric_infos

    def _convert_metric_info_to_qdrant(self, metric_info: MetricInfoMySQL):
        return MetricInfoQdrant(
            id=metric_info.id,
            name=metric_info.name,
            description=metric_info.description,
            relevant_columns=metric_info.relevant_columns,
            alias=metric_info.alias,
        )

    async def _save_metric_info_to_qdrant(self, metric_infos: list[MetricInfoMySQL]):
        # 1.确保存储指标向量信息的集合存在
        await self.metric_qr.ensure_collection()

        points: list[dict] = []

        # 2.遍历指标信息, 对常用字段构建向量索引
        # 每个指标需要构建3次payload相同的向量索引: name, description, alias
        for metric_info in metric_infos:
            points.append(  # name
                {
                    "id": uuid.uuid4(),
                    "embedding_text": metric_info.name,
                    "payload": self._convert_metric_info_to_qdrant(metric_info),
                }
            )
            points.append(  # description
                {
                    "id": uuid.uuid4(),
                    "embedding_text": metric_info.description,
                    "payload": self._convert_metric_info_to_qdrant(metric_info),
                }
            )
            for alia in metric_info.alias:  # alias
                points.append(
                    {
                        "id": uuid.uuid4(),
                        "embedding_text": alia,
                        "payload": self._convert_metric_info_to_qdrant(metric_info),
                    }
                )

        # 获取所有的ids, payloads, embedding_texts
        ids, payloads, embedding_texts = [], [], []
        for point in points:
            ids.append(point["id"])
            payloads.append(point["payload"])
            embedding_texts.append(point["embedding_text"])

        # embedding_texts -> embeddings
        embeddings = []
        batch_size = 10
        for i in range(0, len(embedding_texts), batch_size):
            batch_embedding_texts = embedding_texts[i : i + batch_size]
            embedding = await self.embeddings.aembed_documents(batch_embedding_texts)
            embeddings += embedding

        await self.metric_qr.upsert_metric(ids, embeddings, payloads)
        logger.info(f"已保存指标向量到qdrant数据库: {len(ids)}")
