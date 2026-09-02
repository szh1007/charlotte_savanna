import asyncio

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.conf.app_config import DBConfig, app_config


class MysqlClientManager:
    def __init__(self, db_config: DBConfig):
        self.config = db_config
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker[AsyncSession] | None = None

    def _get_url(self) -> URL:
        """
        构造数据库连接 URL

        通过 URL.create 组件化拼接, 自动转义特殊字符
        (密码中含 @ / : / 等字符时, 字符串拼接的 URL 会解析错误)
        """
        return URL.create(
            drivername="mysql+asyncmy",
            username=self.config.user,
            password=self.config.password,
            host=self.config.host,
            port=self.config.port,
            database=self.config.database,
            query={"charset": "utf8mb4"},
        )

    def init(self):
        """初始化数据库连接池和会话工厂"""
        # engine
        # 1. url: 数据库连接字符串
        # 2. pool_size: 连接池最大连接数
        # 3. pool_pre_ping: 心跳检测, 检测到失效连接时自动创建新连接替换
        self.engine = create_async_engine(
            url=self._get_url(),
            pool_size=5,
            pool_pre_ping=True,
        )

        # session_factory
        # 1. bind: 绑定 engine
        # 2. autoflush: 查询前未提交的修改自动同步缓冲区, 使查询可见最新数据(不提交事务)
        # 3. autobegin: 自动开启事务 (后续可能需要手动开启事务)
        # 4. expire_on_commit: commit 后不过期对象, 但值可能已与数据库不一致, 需重新获取
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=True,
            autobegin=True,
            expire_on_commit=False,
        )

    def session(self) -> AsyncSession:
        """
        获取数据库会话, 使用前必须先调用 init() 初始化工厂

        Raises:
            RuntimeError: session_factory 未初始化时抛出
        """
        if self.session_factory is None:
            raise RuntimeError("session_factory 未初始化, 请先调用 init()")
        return self.session_factory()

    async def close(self):
        """
        释放数据库连接池并重置状态 (需先调用 init())

        Raises:
            RuntimeError: engine 未初始化时抛出
        """
        if self.engine is None:
            raise RuntimeError("engine 未初始化, 请先调用 init()")
        await self.engine.dispose()
        self.engine = None
        self.session_factory = None


dw_client = MysqlClientManager(app_config.db_dw)

meta_client = MysqlClientManager(app_config.db_meta)


if __name__ == "__main__":
    # 初始化
    dw_client.init()

    async def test():
        # 获取 session, 执行操作
        async with dw_client.session() as session:
            # 定义 SQL
            sql = "select * from fact_order limit 10"
            # 执行 SQL
            result = await session.execute(text(sql))
            # 获取结果 (fetchall 不是"复制" 而是"剪切")
            rows = result.mappings().fetchall()

            print(type(rows))
            print(type(rows[0]))
            print(rows[0]["order_id"])

        await dw_client.close()

    asyncio.run(test())
