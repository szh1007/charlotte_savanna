import asyncio
import random
import uuid

from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import QdrantConfig, app_config


class QdrantClient:
    def __init__(self, config: QdrantConfig):
        self.config = config
        self.client: AsyncQdrantClient | None = None

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        self.client = AsyncQdrantClient(url=self._get_url())

    async def close(self):
        await self.client.close()


qdrant_client = QdrantClient(app_config.qdrant)


if __name__ == "__main__":
    # 初始化并获取客户端
    qdrant_client.init()
    client = qdrant_client.client

    async def test():
        # 判断集合是否存在
        if not await client.collection_exists("test_collection"):
            # 不存在则创建集合
            await client.create_collection(
                collection_name="test_collection",
                vectors_config=models.VectorParams(
                    size=app_config.qdrant.embedding_size,
                    distance=models.Distance.COSINE,
                ),
            )

        # 存储数据
        await client.upsert(
            collection_name="test_collection",
            points=[
                models.PointStruct(
                    id=uuid.uuid4(),
                    payload={
                        "data": f"test_{i}",
                    },
                    vector=[random.random() for _ in range(1024)],
                )
                for i in range(10)
            ],
        )

        # 查询数据
        points = await client.query_points(
            collection_name="test_collection",
            query=[random.random() for _ in range(1024)],
            limit=3,
            score_threshold=0.7,
        )

        for point in points.points:
            print(point)
            print(point.payload)

        # 释放资源
        await qdrant_client.close()

    asyncio.run(test())
