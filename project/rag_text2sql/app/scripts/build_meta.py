import asyncio
from pathlib import Path

from app.clients.embedding import embedding_client
from app.clients.mysql import dw_client, meta_client
from app.clients.qdrant import qdrant_client
from app.repositories.mysql.dw import DwMysqlRepository
from app.repositories.mysql.meta import MetaMysqlRepository
from app.repositories.qdrant.column import ColumnQdrantRepository
from app.services.meta import MetaService


async def build(config_path: Path):
    # 1.初始化客户端
    dw_client.init()
    meta_client.init()
    qdrant_client.init()
    embedding_client.init()

    # 2.获取session
    async with dw_client.session() as dw_session, meta_client.session() as meta_session:
        # 3.创建repository
        dw_mysql_repository = DwMysqlRepository(dw_session)
        meta_mysql_repository = MetaMysqlRepository(meta_session)
        column_qdrant_repository = ColumnQdrantRepository(qdrant_client.client)

        # 4.创建service
        meta_service = MetaService(
            dw_mysql_repository=dw_mysql_repository,
            meta_mysql_repository=meta_mysql_repository,
            column_qdrant_repository=column_qdrant_repository,
            embeddings=embedding_client.embeddings,
        )

        # 5.构建元数据
        await meta_service.build(config_path)

    # 6.释放资源
    await dw_client.close()
    await meta_client.close()
    await qdrant_client.close()


if __name__ == "__main__":
    config_path = Path(__file__).parents[2] / "conf" / "meta_config.yaml"
    asyncio.run(build(config_path))
