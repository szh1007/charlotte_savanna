from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import app_config
from app.models.qdrant import ColumnInfoQdrant


class ColumnQdrantRepository:
    collection_name = app_config.qdrant.collection_name_column

    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def ensure_collection(self):
        """
        确保存储字段向量的集合存在且为空
        每次构建先删除旧集合再全量重建
        """
        if await self.client.collection_exists(self.collection_name):
            await self.client.delete_collection(self.collection_name)

        await self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=app_config.qdrant.embedding_size,
                distance=models.Distance.COSINE,
            ),
        )

    async def upsert_column(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        payloads: list[ColumnInfoQdrant],
        batch_size: int = 10,
    ):
        """
        为字段构建向量索引 points = [(id, vector / embedding, payload), ...]

        Args:
            ids: 字段编号列表
            embeddings: 字段向量列表
            payloads: 字段元数据列表
            batch_size: 批次大小, 默认10个

        Returns:
            None: 无返回值, 直接保存到qdrant数据库
        """
        zipped = list(zip(ids, embeddings, payloads))

        for i in range(0, len(zipped), batch_size):
            batch_zipped = zipped[i : i + batch_size]
            points = [
                models.PointStruct(
                    id=id,
                    vector=embedding,
                    payload=payload,
                )
                for id, embedding, payload in batch_zipped
            ]
            await self.client.upsert(self.collection_name, points)

    async def search(
        self,
        embedding: list[float],
        score_threshold: float = 0.6,  # 得分阈值 (需要根据大量测试调整)
    ) -> list[dict]:
        """
        字段召回

        Args:
            embedding: 字段向量, 用于召回
            score_threshold: 得分阈值, 用于筛选召回结果, 默认0.6

        Returns:
            list[ColumnInfoQdrant]: 召回的字段元数据列表
        """
        points = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            score_threshold=score_threshold,
        )
        return [ColumnInfoQdrant(**point.payload) for point in points.points]
