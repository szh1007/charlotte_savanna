import asyncio

from elasticsearch import AsyncElasticsearch

from app.conf.app_config import ESConfig, app_config


class EsClient:
    def __init__(self, config: ESConfig):
        self.config = config
        self.client: AsyncElasticsearch | None = None

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        self.client = AsyncElasticsearch(hosts=[self._get_url()])

    async def close(self):
        await self.client.close()


es_client = EsClient(app_config.es)


if __name__ == "__main__":
    # 初始化并获取ES客户端对象
    es_client.init()
    client = es_client.client

    async def test():
        # 判断指定索引是否存在
        if not await client.indices.exists(index="mybook"):
            # 不存在, 则创建索引+映射 (静态创建, 可配置分词器)
            # type - text: 支持分词 (keyword 不支持分词)
            # analyzer: 建立索引时的分词器(细粒度, 尽可能覆盖查询)
            # search_analyzer: 查询时的分词器(粗粒度, 不要截断语义)
            await client.indices.create(
                index="mybook",
                mappings={
                    "dynamic": False,
                    "properties": {
                        "name": {
                            "type": "text",
                            "analyzer": "ik_max_word",
                            "search_analyzer": "ik_smart",
                        },
                        "author": {"type": "text"},
                        "release_date": {"type": "date", "format": "yyyy-MM-dd"},
                        "page_count": {"type": "integer"},
                    },
                },
            )

        # 添加文档到索引库(批量添加)
        # 关键: _id 取业务唯一键(书名), 重复执行时按 _id 覆盖旧文档, 避免数据堆积
        await client.bulk(
            operations=[
                {"index": {"_index": "mybook", "_id": "revelation-space"}},
                {
                    "name": "Revelation Space",
                    "author": "Alastair Reynolds",
                    "release_date": "2000-03-15",
                    "page_count": 585,
                },
                {"index": {"_index": "mybook", "_id": "1984"}},
                {
                    "name": "1984",
                    "author": "George Orwell",
                    "release_date": "1985-06-01",
                    "page_count": 328,
                },
                {"index": {"_index": "mybook", "_id": "fahrenheit-451"}},
                {
                    "name": "Fahrenheit 451",
                    "author": "Ray Bradbury",
                    "release_date": "1953-10-15",
                    "page_count": 227,
                },
                {"index": {"_index": "mybook", "_id": "brave-new-world"}},
                {
                    "name": "Brave New World",
                    "author": "Aldous Huxley",
                    "release_date": "1932-06-01",
                    "page_count": 268,
                },
                {"index": {"_index": "mybook", "_id": "handmaids-tale"}},
                {
                    "name": "The Handmaids Tale",
                    "author": "Margaret Atwood",
                    "release_date": "1985-06-01",
                    "page_count": 311,
                },
            ],
            refresh=True,
        )

        # 根据关键字检索数据
        resp = await client.search(
            index="mybook",
            query={"match": {"name": "Brave"}},
        )
        print(type(resp), "\n", resp)

        # 解析结果
        for res in resp["hits"]["hits"]:
            print(res["_source"])

        await es_client.close()

    asyncio.run(test())
