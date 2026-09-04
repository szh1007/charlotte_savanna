from elasticsearch import AsyncElasticsearch

from app.conf.app_config import app_config
from app.models.es import ValueInfoEs


class ColumnEsRepository:
    es_index_name = app_config.es.index_name

    es_index_mappings = {
        "dynamic": False,
        "properties": {
            "id": {"type": "keyword"},
            "value": {
                "type": "text",
                "analyzer": "ik_max_word",
                "search_analyzer": "ik_max_word",  # 测试后确认
            },
            "type": {"type": "keyword"},
            "column_id": {"type": "keyword"},
            "column_name": {"type": "keyword"},
            "table_id": {"type": "keyword"},
            "table_name": {"type": "keyword"},
        },
    }

    def __init__(self, client: AsyncElasticsearch):
        self.client = client

    async def ensure_index(self):
        """
        确保存储字段取值的索引存在且为空
        每次构建先删除旧索引再全量重建
        """
        if await self.client.indices.exists(index=self.es_index_name):
            await self.client.indices.delete(index=self.es_index_name)

        await self.client.indices.create(
            index=self.es_index_name,
            mappings=self.es_index_mappings,
        )

    async def save_column_values(self, value_infos: list[ValueInfoEs], batch_size=20):
        """
        保存字段值到ES

        Args:
            value_infos: 字段值列表
            batch_size: 批次大小

        Returns:
            None: 无返回值, 直接保存到ES
        """
        for i in range(0, len(value_infos), batch_size):
            batch_value_infos = value_infos[i : i + batch_size]

            operations = []
            for batch_value_info in batch_value_infos:
                operations.append({"index": {"_index": self.es_index_name}})  # 索引声明
                operations.append(batch_value_info)  # 取值数据

            await self.client.bulk(operations=operations)
