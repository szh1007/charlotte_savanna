import uuid

from langchain_openai import OpenAIEmbeddings
from Lib.pathlib import Path
from omegaconf import OmegaConf

from app.conf.meta_config import MetaConfig, TableConfig
from app.core.log import logger
from app.models.mysql import ColumnInfoMySQL, TableInfoMySQL
from app.models.qdrant import ColumnInfoQdrant
from app.repositories.mysql.dw import DwMysqlRepository
from app.repositories.mysql.meta import MetaMysqlRepository
from app.repositories.qdrant.column import ColumnQdrantRepository


class MetaService:
    def __init__(
        self,
        dw_mysql_repository: DwMysqlRepository,
        meta_mysql_repository: MetaMysqlRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        embeddings: OpenAIEmbeddings,
    ):
        # mysql
        self.dw_mr = dw_mysql_repository
        self.meta_mr = meta_mysql_repository

        # qdrant
        self.column_qr = column_qdrant_repository

        # embeddings
        self.embeddings = embeddings

    async def build(self, config_path: Path):
        # 1.加载配置文件
        context = OmegaConf.load(config_path)
        schema = OmegaConf.structured(MetaConfig)
        meta_config: MetaConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))
        logger.info(f"配置文件加载完成: {config_path}")

        # 2.表信息的索引构建
        if meta_config.tables:
            # 2.1 保存表信息和字段信息到meta数据库
            column_infos = await self._save_table_info_to_meta_db(meta_config.tables)
            # 2.2 保存字段信息到qdrant向量数据库
            await self._save_column_info_to_qdrant(column_infos)

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
