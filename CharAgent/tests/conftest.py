"""CharAgent 测试共享 fixtures: 模型适配器实例 + 本地存储接入.

两条原则:
- **默认用例不依赖外部服务**: 模型走 respx 拦截 (chat_model), checkpoint 走内存版
  或假客户端 —— 只有标了 `pg` / `redis` 的用例才需要本机服务, 且默认被 addopts
  排除 (见 pytest.ini), 想看真实存储时用 `pytest -m pg` / `pytest -m redis` 单跑.
- **连不上就跳过并说明原因**: 本机没起服务时给一句可执行的提示, 而不是一串连接
  失败 (那是排查该输出的东西, 不是测试).

本文件里的数据库操作一律**同步** psycopg: 异步连接在 Windows 的默认事件循环
(Proactor) 上跑不起来, 理由与实现一致 (见 checkpoint/postgres.py 的 docstring).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from helpers import API_KEY, BASE_URL
from sqlalchemy import create_engine, pool, text

from CharAgent.checkpoint.config import postgres_dsn
from CharAgent.checkpoint.postgres import PostgresCheckpointSaver
from CharAgent.checkpoint.utils.errors import CheckpointConfigError
from CharAgent.db import PgDatabase
from CharAgent.db.schema import threads
from CharAgent.model import HttpXChatModel


@pytest_asyncio.fixture
async def chat_model():
    """构造真实适配器实例 (模型名与 .env 对齐), 测试用 respx 拦截其网络请求."""
    model = HttpXChatModel(api_key=API_KEY, base_url=BASE_URL, model="deepseek-flash")
    yield model
    await model.aclose()


def _load_root_dotenv() -> None:
    """把仓库根 .env 读进环境变量 (override=False: 已有的真环境变量优先).

    只有需要外部服务的用例才用得上, 所以按需读取 —— 不让「跑单测」这件事依赖
    .env 存在.
    """
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


def postgres_test_dsn():
    """本机 Postgres 连接串 (SQLAlchemy 的 URL 对象); 拿不到配置就跳过用例.

    给「按需连 PG」的用例用 (比如双实现对比: 内存与 Redis 那两组参数不需要 PG,
    只有 PG 那组才该受影响).
    """
    _load_root_dotenv()
    try:
        return postgres_dsn(os.environ)
    except CheckpointConfigError as exc:
        pytest.skip(f"本机没配 Postgres 连接信息, 跳过 PG 用例: {exc}")


@pytest.fixture(scope="session")
def pg_dsn():
    """本机 Postgres 连接串 (优先专用 DSN, 否则由 PGSQL_* 拼)."""
    return postgres_test_dsn()


def conninfo(dsn) -> str:
    """把连接串转成 psycopg 认的文本形式.

    为什么不能直接把 `postgres_dsn()` 的结果交给 psycopg: 它返回的是 SQLAlchemy
    的 URL 对象, 而 psycopg 只认字符串或它自己的 `Conninfo` —— 两种形式的 URL
    都报 `AttributeError: 'URL' object has no attribute 'encode'` (2026-09-14
    实测). 于是这里转两处:

    - `render_as_string(hide_password=False)`: 默认渲染会把密码换成三个星号,
      而 psycopg 要真密码
    - 去掉驱动名 `+psycopg`: 那是 SQLAlchemy 用来挑驱动的写法, psycopg 不认识
      (报 `missing "=" after "postgresql+psycopg://..."`)

    这个帮助函数只在测试里用 (裸连库对账). 生产代码不碰 psycopg —— 快照存储与
    仓储都走 SQLAlchemy.
    """
    if isinstance(dsn, str):
        return dsn
    return dsn.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def sqlalchemy_test_url(env=None):
    """同一条连接, 换成 SQLAlchemy 认的 URL 形式 (带 +psycopg 驱动名).

    与 `conninfo` 是同一份配置的两种渲染: psycopg 要 `postgresql://...` 文本,
    SQLAlchemy 要带驱动名的 URL. 两边都从 `postgres_test_dsn()` 出发, 于是
    「测的是同一个库」这件事不靠人记.

    顺带把根 .env 读进来 (与 `postgres_test_dsn` 一样按需加载) —— 单独调
    `db.config.sqlalchemy_url()` 不会自己读 .env, 拿到的是「一个变量都没有」.
    """
    dsn = postgres_test_dsn()
    return dsn.set(drivername="postgresql+psycopg")


# ---------------------------------------------------------------------------
# db 层的测试 schema (ticket 17 起两个文件共用: 仓储用例与「端点读记录表」那条路)
# ---------------------------------------------------------------------------

# 开发机是共享库 (本项目各子项目共用), 而 db 用例要建表、要跑迁移、还要删表 ——
# 挤在 public 里会与手工建的表互相干扰, 也会让 alembic 撞上「表已存在」. 于是
# 每个用例在**独立 schema** 里干活, 用完整个删掉 (表 / 索引 / 外键全没, 不留痕迹).
TEST_SCHEMA = "charagent_test"


@pytest.fixture(scope="session")
def _admin_url():
    """连到默认 schema 的 URL (用来建 / 删测试 schema)."""
    return sqlalchemy_test_url()


def _run_sql(url, *statements: str) -> None:
    """同步跑几条 SQL (放进线程里执行, 别卡事件循环).

    用 NullPool: 每次跑完就关连接 —— 建/删 schema 是低频动作, 留着池子没意义.
    """
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def db(_admin_url) -> AsyncIterator[PgDatabase]:
    """指向测试 schema 的 PgDatabase, 用完把测试 schema 整个删掉.

    **整个 schema 删掉**是最干净的收尾: 表、索引、外键全没了, 不留任何痕迹,
    也不担心漏删哪张. 拿不到 PG 时代码在 `_admin_url` 那里就跳过 (见
    `postgres_test_dsn`) —— 用例不必自己判环境.
    """
    await asyncio.to_thread(
        _run_sql,
        _admin_url,
        f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE",
        f"CREATE SCHEMA {TEST_SCHEMA}",
    )
    # 引擎要 SQLAlchemy 的 URL (带 +psycopg 驱动名), 而 `_run_sql` 那条路走
    # psycopg 需要的文本形式 —— 两种形式各用各的, 别混
    engine = create_engine(
        sqlalchemy_test_url(),
        # 每条连接都先切到测试 schema —— 仓储与迁移写的 SQL 里都不带 schema 前缀,
        # 于是它们自然落在隔离区里
        connect_args={"options": f"-csearch_path={TEST_SCHEMA}"},
    )
    database = PgDatabase(engine=engine)
    # 先把表建好: 这是每个用例的起跑线 (刚删过 schema, 表一定是没有的).
    # 查表建表这件事本身另有用例专门验 (test_create_tables_is_idempotent).
    await database.create_tables()
    try:
        yield database
    finally:
        await asyncio.to_thread(engine.dispose)
        await asyncio.to_thread(
            _run_sql, _admin_url, f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"
        )


def _ensure_thread_row(url, thread_id: str) -> None:
    """给这个会话号建一行 threads (同步执行, 由调用方放进线程).

    ticket 24 起帧的 `thread_id` 有了外键 (ON DELETE CASCADE), 于是「只写快照、
    不建会话行」不再可行 —— 这一步就是那条行为契约在测试里的落地.

    连表一起建 (`checkfirst=True`, 用的就是 db/schema.py 那份定义): 新环境里表
    可能还没建, 而本 fixture 不保证排在 `pg_saver` 后面 (先后由用例签名决定),
    少这一步就会把「表还没建」变成一个外键错误 —— 报错与真因毫无关系.
    """
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.begin() as connection:
            threads.create(connection, checkfirst=True)
            connection.execute(
                threads.insert().values(
                    thread_id=thread_id,
                    tenant_id="test-tenant",
                    user_id="test-user",
                )
            )
    finally:
        engine.dispose()


def _delete_thread_row(pg_dsn, thread_id: str) -> None:
    """删掉这个会话的行 (它的帧跟着 CASCADE 走; 同步执行, 由调用方放进线程)."""
    try:
        with psycopg.connect(conninfo(pg_dsn), autocommit=True) as conn:
            # 表名从 Table 对象上取 (不抄字面量): schema.py 那份定义是唯一来源,
            # 这里抄一份就又多了个会漂移的地方 —— 上面那句 insert 用的就是它
            conn.execute(
                f"DELETE FROM {threads.name} WHERE thread_id = %s", (thread_id,)
            )
    except psycopg.errors.UndefinedTable:
        # 表还不存在 = 这次运行根本没写过库, 本来就没东西要删
        return


@pytest_asyncio.fixture
async def pg_thread_id(request) -> AsyncIterator[str]:
    """发一个**唯一**的会话号, 用完把这个会话 (连同它的帧) 从表里删掉.

    唯一是关键: 用例之间靠会话号隔离, 不必清空整张表 (开发库里可能还有别人
    —— 比如手工演示 —— 留下的数据, 不能顺手抹掉).

    **标了 `pg` 的用例还会得到一行 threads** (ticket 24): 帧的 `thread_id` 有外键
    之后, 写 PG 的用例必须先有会话行; 而内存 / Redis 那几组参数 (同一个 fixture
    也被 test_checkpoint_backends 的参数化用着) 不该因为共用一个 fixture 就往
    开发库里写一行. 判据用**标记**而不是「签名里有没有 pg_saver」: 标记是 pytest
    的正规表达 (参数化上挂的 `marks=` 也算), fixture 名字则是内部实现.

    这个 fixture 没配 Postgres 时**只发号不连库**: 建行与删行都静默跳过 ——
    于是内存 / Redis 的用例照样能用它拿一个干净的分区键.
    """
    thread_id = f"test-{uuid4().hex}"
    _load_root_dotenv()
    try:
        dsn = postgres_dsn(os.environ)
    except CheckpointConfigError:
        yield thread_id
        return
    if request.node.get_closest_marker("pg") is not None:
        await asyncio.to_thread(_ensure_thread_row, dsn, thread_id)
    yield thread_id
    await asyncio.to_thread(_delete_thread_row, dsn, thread_id)


@pytest_asyncio.fixture
async def pg_saver(pg_dsn) -> AsyncIterator[PostgresCheckpointSaver]:
    """接了本机 Postgres 的 saver (顺带把表建好: 建表是幂等的)."""
    saver = PostgresCheckpointSaver(dsn=pg_dsn)
    await saver.ensure_schema()
    yield saver
    await saver.aclose()
