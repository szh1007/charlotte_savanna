"""数据库连接层: 一套同步引擎 + 异步门面, 供仓储与快照存储共用.

一句话理解: 这个文件管**怎么连库、怎么开一次事务**. 上层 (仓储) 只写「要做什么」,
不操心连接从哪来、出错怎么回滚、什么时候关.

**为什么是「同步引擎 + asyncio.to_thread」而不是 SQLAlchemy 的 async 引擎**
(本机实测, 与 checkpoint 包当初踩的是同一个坑):

    psycopg 的异步连接在 Windows 默认的 ProactorEventLoop 上直接报错:
    `InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async
    mode`. SQLAlchemy 的 `create_async_engine` 底层就是它, 同样跑不起来
    (2026-09-14 实测). 而换事件循环是**给使用方加前置条件** —— 本项目在 Windows
    上开发与演示, 不能要求调用方先改 asyncio 的策略.

    于是改走: **同步 Engine + `asyncio.to_thread`**. 对外仍是 async 接口 (不阻塞
    事件循环 —— 阻塞的数据库调用被丢进工作线程), 里面是老老实实的同步驱动.
    Django 的 async ORM (`sync_to_async`) 走的就是这条路, 是异步框架里用同步
    驱动的标准做法. 并发靠 SQLAlchemy 自带的连接池 (不像早期那样用一条
    连接 + 一把锁排队 —— 那是权宜之计, 有了池子就不需要了).

**一次 `connect()` = 一次事务**: 正常退出自动提交, 中途抛异常自动回滚. 于是
「建运行 + 写用户消息」这类必须同生共死的操作, 在一个 `async with` 里就能保证
要么都成、要么都不留 —— 不会出现「运行建好了但消息没写进去」这种半截数据.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import URL, Engine, Table, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from CharAgent.db.config import echo_enabled, sqlalchemy_url
from CharAgent.db.errors import DataStoreError


class PgDatabase:
    """Postgres 连接与事务的入口 (上层拿到它就能干活).

    引擎是**懒建立**的: 构造时不连库, 第一次用才连 —— 于是「造一个 database
    对象」不需要数据库在线 (配置对不对, 等真用的时候报错也不迟).

    用法::

        db = PgDatabase()                       # 从环境变量读连接串
        async with db.connect() as session:     # 一次事务
            session.execute(...)                # 正常退出自动提交
        await db.dispose()                      # 收尾: 关掉连接池

    Args:
        url: 连接配置 —— SQLAlchemy 的 `URL` 对象或它的文本形式都收; None 表示
            从环境变量读 (见 config.py).
        engine: 注入一个现成的引擎 (复用别人的连接池); 给了它就**不看 url**,
            且 `dispose()` 不会关它 (谁建的谁关).
        echo: 是否把 SQL 打到日志; None 表示读 CHARAGENT_DB_ECHO.

    注意**构造期不校验、不连库**: 连接串配错了也不会在这里报错, 而是等你第一次
    `engine()` / `connect()` 时才抛 `DataConfigError`(配置缺) 或 `DataStoreError`
    (连不上) —— 「造一个 database 对象」这个动作不该依赖数据库在线.
    """

    def __init__(
        self,
        *,
        url: str | URL | None = None,
        engine: Engine | None = None,
        echo: bool | None = None,
    ) -> None:
        self._url = url
        self._echo = echo_enabled() if echo is None else echo
        self._engine: Engine | None = engine
        # 这个引擎是不是本类建的 (注入的不关 —— 谁建的谁负责)
        self._owns_engine = engine is None
        self._session_factory: sessionmaker[Session] | None = None

    @property
    def url(self) -> Any:
        """连接串 (构造时给了 url / engine 就用那个, 否则从环境变量读).

        公开它是因为排查时常常需要**连接串本身** (手工连库对账、跑 alembic),
        与其让各处重复调一遍配置函数, 不如从这里取.
        """
        if self._url is not None:
            return self._url
        if self._engine is not None:
            return self._engine.url
        return sqlalchemy_url()

    def engine(self) -> Engine:
        """拿到 (必要时先建好) 同步引擎.

        为什么允许外部拿引擎: alembic 的 `env.py` 与快照存储需要**引擎本身**
        (前者要跑迁移, 后者要在同一套连接池上工作), 它们不该各自再建一个池.

        注意这是**同步**引擎 (理由见模块 docstring): 拿到它的人如果直接调用,
        会阻塞事件循环 —— 唯一的例外是 alembic 迁移脚本 (命令行工具, 没有事件
        循环在跑, 阻塞正是它要的).
        """
        if self._engine is None:
            self._engine = create_engine(
                self.url,
                echo=self._echo,
                # 借用前先探一下: 连接被数据库单方面掐断 (超时 / 重启) 时, 池里
                # 那条死连接会被发现并换掉, 而不是把「连接已关闭」的错抛给上层
                pool_pre_ping=True,
            )
        return self._engine

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[Session]:
        """开一次事务 (正常退出提交, 抛异常回滚), 交出 SQLAlchemy 的 Session.

        Yields:
            Session: 事务里的会话 —— 仓储的所有读写都在它上面做.

        Raises:
            DataStoreError: **连不上库** / 事务体里的 SQL 失败 / 提交失败.
                原始异常经 `from` 保留 (排查时看不丢).

        为什么连「连不上库」也包进来 (2026-09-14 审计修正): 借连接与开事务那次
        调用原先在 `try` 之外, 于是连接失败会抛**原生**的
        `sqlalchemy.exc.OperationalError` —— 而「库连不上」恰恰是最常见的故障,
        上层只 `except DataStoreError` 会正好漏掉它. 现在整段都在 try 里
        (用一个 `session is not None` 判断区分「连都没连上」与「连上了才出错」).
        """
        factory = self._session_factory
        if factory is None:
            factory = sessionmaker(
                bind=self.engine(),
                # 自动 flush 关掉: 什么时候把改动发给数据库由我们显式决定,
                # 免得一条无关的 SELECT 触发一堆看不见的写入
                autoflush=False,
                expire_on_commit=False,
            )
            self._session_factory = factory

        session: Session | None = None
        try:
            # 借连接 + 开事务也在 try 里: 这一步会真的去连库, 各种连不上的错
            # (口令不对 / 库没起 / 网络不通) 都在这里冒出来
            session = await asyncio.to_thread(self._open, factory)
            yield session
        except SQLAlchemyError as exc:
            # 出错路径: 连上了就回滚掉这次事务里已发出去的一切 (不留半截数据),
            # 再抛出统一异常. 没连上就没得回滚.
            if session is not None:
                await self._discard(session)
            raise DataStoreError(f"数据库操作失败: {exc}") from exc
        except BaseException:
            # 非 SQL 的异常 (取消 / 业务自己的错): 同样回滚, 但**原样抛** ——
            # 调用方看到的是它自己那个错, 不是被包过一层的东西
            if session is not None:
                await self._discard(session)
            raise
        else:
            # 正常路径: 提交. 提交本身也可能失败 (约束冲突 / 连接断了)
            await self._commit(session)

    async def create_tables(self, tables: Sequence[Table] | None = None) -> None:
        """把表建出来 (已存在的跳过 —— 幂等, 重复调用安全).

        表从哪儿来: 不传就取 `db/schema.py` 的 `ALL_TABLES` (本包那六张) ——
        database 层只负责「怎么连库」, 表定义仍只有 schema 一处 (这里的延迟
        import 就是为了不让连接层反向依赖表定义层).

        为什么用 SQLAlchemy 自带的 `create_all` 而不是自己拼 CREATE TABLE:
        `create_all` 自带 `checkfirst` (先查存在性再建), 于是重复调用安全;
        它还会按外键依赖排好建表顺序, 不用手工维护.

        一个前提: **这一批表共用同一个 metadata** —— 建表顺序与外键解析都由
        `metadata` 完成, 所以本方法取第一张表所属的那个容器来干活. 混着传两个
        metadata 的表: 有外键关联时直接报 `NoReferencedTableError` (点名找不到
        的那张表), 不会静默建错; 无外键则各建各的, 无害.

        与 alembic 的分工: 这个方法只保证**表在**, 让本包能独立跑起来 (测试、
        本机演示); 线上环境的表结构演进归 `alembic/` 的迁移脚本管. 两边用的是
        同一份表定义 (schema.py) —— 迁移的 autogenerate 以它为基准, 而**已发布
        的迁移脚本是冻结的历史**, 刻意不 import 它 (见那个脚本的模块 docstring).

        Args:
            tables: 要建的表; None 表示 `db/schema.py` 的 ALL_TABLES
                (本包那六张). 传空列表 = 什么都不做.

        Raises:
            DataStoreError: 建表失败 (权限不足 / 连接断了).
        """
        if tables is None:
            # 延迟 import: database 层是通用的连接层, 默认值指向本包的表定义
            from CharAgent.db.schema import ALL_TABLES

            tables = ALL_TABLES
        picked = list(tables)
        if not picked:
            return
        # 从表自己身上问出它属于哪个 metadata: 连接层因此不必 import schema
        # (不反向依赖表定义层), 拿别的 metadata 的表进来也能用
        tables_shared_metadata = picked[0].metadata

        def run() -> None:
            with self.engine().begin() as connection:
                tables_shared_metadata.create_all(connection, tables=picked)

        try:
            await asyncio.to_thread(run)
        except SQLAlchemyError as exc:
            raise DataStoreError(f"建表失败: {exc}") from exc

    async def dispose(self) -> None:
        """关掉连接池 (之后再用会自动重建一个新的).

        进程退出前调一次: 让池子里的连接干净地还给数据库, 而不是等它超时.

        **注入进来的引擎不关**: 谁建的谁负责 —— 注入方 (比如测试里共享一个引擎)
        可能还在用它干别的活.
        """
        engine = self._engine
        self._session_factory = None
        if engine is not None and self._owns_engine:
            self._engine = None
            await asyncio.to_thread(engine.dispose)

    # ------------------------------------------------------------------
    # 内部: 把同步的数据库活儿挪到线程里做
    # ------------------------------------------------------------------

    @staticmethod
    def _open(factory: sessionmaker[Session]) -> Session:
        """在工作线程里: 借连接 → 开事务 → 交出会话.

        两步各管一件事:
        - `factory()` 从连接池借一条连接 (必要时新建)
        - `session.begin()` 显式开事务 —— 一次 `connect()` 就是一个事务边界

        **为什么有了连接池还要自己管会话**: 池子解决的是「连接复用」, 不解决
        「这几条语句算不算一件事」. 事务边界必须显式画出来, 否则「建运行 + 写
        消息」中间挂掉会留下半截数据.
        """
        session = factory()
        session.begin()
        return session

    @staticmethod
    def _commit_sync(session: Session) -> None:
        """提交并还连接 (同步执行, 由调用方放进线程)."""
        try:
            session.commit()
        finally:
            session.close()

    @staticmethod
    def _discard_sync(session: Session) -> None:
        """回滚并还连接 (同步执行, 由调用方放进线程).

        回滚本身失败也要把连接还掉 —— 否则这次失败会连累整个池子 (连接泄漏).
        """
        try:
            session.rollback()
        finally:
            session.close()

    async def _commit(self, session: Session) -> None:
        """提交这次事务.

        Raises:
            DataStoreError: 提交失败 (约束冲突 / 连接断了 / 库拒了).
        """
        try:
            await asyncio.to_thread(self._commit_sync, session)
        except SQLAlchemyError as exc:
            raise DataStoreError(f"提交事务失败: {exc}") from exc

    async def _discard(self, session: Session) -> None:
        """回滚这次事务并还连接 (出错路径的收尾, 本身不抛错)."""
        # 回滚失败不该盖掉真正的错误 —— 上面那个异常才是要报给调用方的,
        # 所以这里**有意吞掉** (suppress 把「有意」这件事写在明面上)
        with contextlib.suppress(SQLAlchemyError):
            await asyncio.to_thread(self._discard_sync, session)
