"""CharAgent 测试共享 fixtures: 模型适配器实例 + 本地存储接入 (issue 07).

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

from CharAgent.checkpoint.config import postgres_dsn
from CharAgent.checkpoint.postgres import PostgresCheckpointSaver
from CharAgent.checkpoint.utils.ddl import CHECKPOINTS_TABLE
from CharAgent.checkpoint.utils.errors import CheckpointConfigError
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


def postgres_test_dsn() -> str:
    """本机 Postgres 连接串; 拿不到配置就跳过用例.

    给「按需连 PG」的用例用 (比如双实现对比: 内存与 Redis 那两组参数不需要 PG,
    只有 PG 那组才该受影响).
    """
    _load_root_dotenv()
    try:
        return postgres_dsn(os.environ)
    except CheckpointConfigError as exc:
        pytest.skip(f"本机没配 Postgres 连接信息, 跳过 PG 用例: {exc}")


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    """本机 Postgres 连接串 (优先 CHECKPOINT_POSTGRES_DSN, 否则由 PGSQL_* 拼)."""
    return postgres_test_dsn()


def _delete_thread_frames(pg_dsn: str, thread_id: str) -> None:
    """删掉某个会话的全部快照 (同步执行, 由调用方放进线程)."""
    try:
        with psycopg.connect(pg_dsn, autocommit=True) as conn:
            conn.execute(
                f"DELETE FROM {CHECKPOINTS_TABLE} WHERE thread_id = %s", (thread_id,)
            )
    except psycopg.errors.UndefinedTable:
        # 表还不存在 = 这次运行一帧都没存过, 本来就没东西要删
        return


@pytest_asyncio.fixture
async def pg_thread_id() -> AsyncIterator[str]:
    """发一个**唯一**的会话号, 用完把这个会话的快照从表里删掉.

    唯一是关键: 用例之间靠会话号隔离, 不必清空整张表 (开发库里可能还有别人
    —— 比如手工演示 —— 留下的数据, 不能顺手抹掉).

    这个 fixture 本身**不连库** (发号复用性): 没配 Postgres 时它就只发一个号,
    收尾时静默跳过删除 —— 于是内存 / Redis 的用例也能用它拿一个干净的分区键.
    """
    thread_id = f"test-{uuid4().hex}"
    yield thread_id
    _load_root_dotenv()
    try:
        dsn = postgres_dsn(os.environ)
    except CheckpointConfigError:
        return
    await asyncio.to_thread(_delete_thread_frames, dsn, thread_id)


@pytest_asyncio.fixture
async def pg_saver(pg_dsn: str) -> AsyncIterator[PostgresCheckpointSaver]:
    """接了本机 Postgres 的 saver (顺带把表建好: 建表是幂等的)."""
    saver = PostgresCheckpointSaver(dsn=pg_dsn)
    await saver.ensure_schema()
    yield saver
    await saver.aclose()
