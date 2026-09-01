import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.conf.app_config import DBConfig, app_config


class MysqlClientManager:
    def __init__(self, db_config: DBConfig):
        self.engine: AsyncEngine | None = None
        self.config = db_config

    def _get_url(self):
        return (
            f"mysql+asyncmy://{self.config.user}:{self.config.password}"
            f"@{self.config.host}:{self.config.port}/{self.config.database}?charset=utf8mb4"
        )

    def init(self):
        self.engine = create_async_engine(self._get_url())

    async def close(self):
        await self.engine.dispose()


dw_mysql_client_manager = MysqlClientManager(app_config.db_dw)

meta_mysql_client_manager = MysqlClientManager(app_config.db_meta)


if __name__ == "__main__":
    # 创建engine
    dw_mysql_client_manager.init()

    async def test():
        # 获取session, 执行操作
        async with AsyncSession(dw_mysql_client_manager.engine) as session:
            # 定义sql
            sql = "select * from fact_order limit 10"

            # 执行sql
            result = await session.execute(text(sql))

            # 获取结果
            rows = result.mappings().fetchall()
            print(type(rows))
            print(type(rows[0]))
            print(rows[0]["order_id"])

        await dw_mysql_client_manager.close()

    asyncio.run(test())
