import asyncio
from dataclasses import dataclass

from sqlalchemy import event, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.conf.app_config import DBConfig, app_config


@dataclass(frozen=True)
class ReadOnlyProfile:
    """dw 连接的只读档案: 连上就把会话钉成只读 + 两层超时.

    C16 的数据库侧兜底 —— 语句级白名单万一漏了, 这一层让写操作报 1792
    (`Cannot execute statement in a READ ONLY transaction.`), 让慢查询在
    `statement_timeout_ms` 内被掐断 (实测报 3024, 且连接仍可用).

    档位的默认值只有一处: `app/conf/app_config.py` 的 `DwGuardConfig`
    (这里不再抄一份, 免得两处各说各话), 所以这几个字段**必填**。

    两个实测出来的细节:
    - **只在这里 SET 一次**: 连接建立时执行, 之后的每个事务都是 READ ONLY;
      不需要 `SET SESSION TRANSACTION READ ONLY` + commit 那套绕法 (那是
      会话中途切换的代价).
    - **asyncmy 没有 `write_timeout`**: 它只认 `read_timeout` / `connect_timeout`
      (票面写的 read/write 双层是按别的驱动写的; 按驱动实际能力落).
    """

    statement_timeout_ms: int
    read_timeout_s: int
    connect_timeout_s: int

    def connect_args(self) -> dict:
        return {
            "read_timeout": self.read_timeout_s,
            "connect_timeout": self.connect_timeout_s,
        }

    def session_statements(self) -> tuple[str, ...]:
        return (
            f"SET SESSION max_execution_time = {self.statement_timeout_ms}",
            "SET SESSION TRANSACTION READ ONLY",
        )


class MysqlClient:
    def __init__(self, db_config: DBConfig, read_only: ReadOnlyProfile | None = None):
        self.config = db_config
        self.read_only = read_only
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
        # 4. connect_args / connect 事件: 带只读档案时给每个连接钉上只读与超时
        self.engine = create_async_engine(
            url=self._get_url(),
            pool_size=5,
            pool_pre_ping=True,
            connect_args=self.read_only.connect_args() if self.read_only else {},
        )
        if self.read_only is not None:
            # 注册在**这台 engine** 上: init/close 反复调用也不会重复挂钩子
            event.listens_for(self.engine.sync_engine, "connect")(
                self._apply_read_only_profile
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

    def _apply_read_only_profile(self, dbapi_connection, connection_record) -> None:
        """连接建立时钉上只读与语句预算 (每个池连接一次).

        失败就**不让连接建立** (fail closed): 钉不上只读的 dw 连接不该被拿去
        执行模型生成的 SQL.实测 asyncmy 上同步游标可用, 两条 SET 都能过.
        """
        if self.read_only is None:  # pragma: no cover - 只有挂了钩子才会走到
            return
        cursor = dbapi_connection.cursor()
        try:
            for statement in self.read_only.session_statements():
                cursor.execute(statement)
        finally:
            cursor.close()

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


# dw: 只读档案常开 —— 这个库只被查询 (建索引时也只读它采集字段类型与取值),
# 写入另有 ETL 通道; 钉上只读 + 超时是 C16 的数据库侧兜底
dw_client = MysqlClient(
    app_config.db_dw,
    read_only=ReadOnlyProfile(
        statement_timeout_ms=app_config.dw_guard.statement_timeout_ms,
        read_timeout_s=app_config.dw_guard.read_timeout_s,
        connect_timeout_s=app_config.dw_guard.connect_timeout_s,
    ),
)

# meta: 建索引时要写 (build_meta), 不挂只读; 但它的 SQL 不经模型, 风险面在 dw 侧
meta_client = MysqlClient(app_config.db_meta)


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
