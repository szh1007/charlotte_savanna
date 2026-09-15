"""checkpoint 配置切换测试 (issue 07 / ADR-0002 的「运行时配置切换」).

关注点是「配置说用哪个后端, 就真的造出哪个」以及「配置写错时立刻报错, 而不是
等到第一次存取才炸」. 这里不连任何库: 三个 saver 都是懒连接, 构造期不碰网络.
"""

from __future__ import annotations

import pytest
from conftest import conninfo
from psycopg.conninfo import conninfo_to_dict

from CharAgent.checkpoint.config import (
    ENV_BACKEND,
    ENV_KEY_PREFIX,
    ENV_REDIS_MAX_FRAMES,
    ENV_REDIS_MODE,
    ENV_REDIS_URL,
    ENV_TTL_SECONDS,
    build_saver,
    checkpoint_saver_from_env,
    postgres_dsn,
)
from CharAgent.checkpoint.memory import InMemoryCheckpointSaver
from CharAgent.checkpoint.postgres import PostgresCheckpointSaver
from CharAgent.checkpoint.redis import RedisCheckpointSaver
from CharAgent.checkpoint.utils.errors import CheckpointConfigError

PGSQL_ENV = {
    "PGSQL_USERNAME": "charlotte",
    "PGSQL_PASSWORD": "pwd",
    "PGSQL_HOST": "127.0.0.1",
    "PGSQL_PORT": "5432",
    "PGSQL_NAME": "charlotte",
}


def test_defaults_to_memory_backend():
    """什么都没配: 用内存版 (最安全的选择 —— 不依赖任何外部服务)."""
    saver = checkpoint_saver_from_env({})

    assert isinstance(saver, InMemoryCheckpointSaver)


def test_reads_backend_from_env():
    """CHARAGENT_CHECKPOINT_BACKEND 说用哪个就用哪个 (这里用 redis, 不必真连)."""
    saver = checkpoint_saver_from_env({ENV_BACKEND: "redis"})

    assert isinstance(saver, RedisCheckpointSaver)


def test_backend_name_is_case_and_space_insensitive():
    """后端名首尾空格与大小写都宽容 (手写 .env 时最容易多敲空格).

    注意只宽容这两个维度, 不做同义词猜测: 写 "postgresql" 会得到「未知后端 +
    可选值列表」的报错 —— 宁可让人看一眼正确写法, 也不替人猜 (猜错就是连错库).
    """
    saver = build_saver("  Postgres  ", PGSQL_ENV)

    assert isinstance(saver, PostgresCheckpointSaver)


def test_backend_synonym_is_rejected_with_options():
    """近似写法 (postgresql) 不猜, 直接报错并给出可选值."""
    with pytest.raises(CheckpointConfigError) as excinfo:
        build_saver("postgresql", PGSQL_ENV)

    assert "postgres" in str(excinfo.value)


def test_unknown_backend_lists_options():
    """后端名不认识: 报错并列出可选值 (别让人去翻文档)."""
    with pytest.raises(CheckpointConfigError) as excinfo:
        build_saver("mysql", {})

    message = str(excinfo.value)
    assert "mysql" in message
    assert "memory" in message and "postgres" in message


def test_redis_backend_falls_back_to_shared_redis_url():
    """没配专用变量时, 用根 .env 里共用的 REDIS_URL (快照与别的组件同一个库)."""
    saver = build_saver("redis", {"REDIS_URL": "redis://10.0.0.9:6380/3"})
    assert isinstance(saver, RedisCheckpointSaver)

    # 看客户端真正的连接参数 —— 这是「配置有没有接上」的唯一证据
    kwargs = saver._client.connection_pool.connection_kwargs
    assert kwargs["host"] == "10.0.0.9"
    assert kwargs["port"] == 6380
    assert kwargs["db"] == 3


def test_redis_backend_prefers_dedicated_url():
    """配了 CHARAGENT_CHECKPOINT_REDIS_URL 就用它 (覆盖共用的那个)."""
    saver = build_saver(
        "redis",
        {
            ENV_REDIS_URL: "redis://127.0.0.1:6379/9",
            "REDIS_URL": "redis://10.0.0.9:6380/3",
        },
    )
    assert isinstance(saver, RedisCheckpointSaver)

    kwargs = saver._client.connection_pool.connection_kwargs
    assert kwargs["db"] == 9


def test_redis_key_prefix_wired_from_env():
    """键前缀来自配置 (键名长相是排查时第一眼要看的东西)."""
    saver = build_saver("redis", {ENV_KEY_PREFIX: "myapp"})
    assert isinstance(saver, RedisCheckpointSaver)

    assert saver.key_for("t-1") == "myapp:ckpt:t-1"


def test_redis_ttl_parsed_from_env():
    """TTL 配置传进 saver (capabilities 会因此变成「会过期」)."""
    saver = build_saver("redis", {ENV_TTL_SECONDS: "3600"})
    assert isinstance(saver, RedisCheckpointSaver)

    assert saver.capabilities.ttl is True


def test_redis_mode_from_env():
    """Redis 模式来自配置: 默认 history (全历史), 显式配置可切 latest."""
    default_saver = build_saver("redis", {})
    assert isinstance(default_saver, RedisCheckpointSaver)
    assert default_saver.capabilities.history is True

    latest_saver = build_saver("redis", {ENV_REDIS_MODE: "latest"})
    assert isinstance(latest_saver, RedisCheckpointSaver)
    assert latest_saver.capabilities.history is False


def test_redis_max_frames_from_env():
    """裁剪帧数来自配置 (history 模式下生效) —— 键前缀与裁剪一起接上."""
    saver = build_saver("redis", {ENV_REDIS_MAX_FRAMES: "5"})
    assert isinstance(saver, RedisCheckpointSaver)

    assert saver.key_for("t-1") == "charagent:ckpt:t-1"


def test_redis_max_frames_must_be_integer():
    """裁剪帧数不是整数: 报错 (不去猜)."""
    with pytest.raises(CheckpointConfigError) as excinfo:
        build_saver("redis", {ENV_REDIS_MAX_FRAMES: "五帧"})

    assert ENV_REDIS_MAX_FRAMES in str(excinfo.value)


def test_redis_ttl_blank_means_no_expiry():
    """TTL 留空 = 不过期 (而不是「0 秒后过期」这种可怕的理解)."""
    saver = build_saver("redis", {ENV_TTL_SECONDS: "   "})
    assert isinstance(saver, RedisCheckpointSaver)

    assert saver.capabilities.ttl is False


def test_redis_ttl_must_be_integer():
    """TTL 不是整数: 报错 (不去猜「大概是多少秒」)."""
    with pytest.raises(CheckpointConfigError) as excinfo:
        build_saver("redis", {ENV_TTL_SECONDS: "一小时"})

    assert ENV_TTL_SECONDS in str(excinfo.value)


def test_postgres_backend_builds_dsn_from_shared_env():
    """没配专用 DSN 时, 用根 .env 共用的 PGSQL_* 拼一个 (字段逐个对上).

    取连接串的位置随实现变过: 快照存储统一到 SQLAlchemy 之后, 连接信息挂在
    `PgDatabase.url` 上 (SQLAlchemy 的 URL 对象). `conninfo_to_dict` 认它
    (URL 实现了 psycopg 要的那套 getter, 且密码会解码回明文而不是 `***`).
    """
    saver = build_saver("postgres", PGSQL_ENV)
    assert isinstance(saver, PostgresCheckpointSaver)

    fields = conninfo_to_dict(conninfo(saver._database.url))
    assert fields["host"] == "127.0.0.1"
    assert fields["port"] == "5432"
    assert fields["user"] == "charlotte"
    assert fields["dbname"] == "charlotte"


def test_postgres_dsn_prefers_dedicated_value():
    """配了 CHARAGENT_CHECKPOINT_POSTGRES_DSN 就用它 (可以指到另一个库).

    返回值是 SQLAlchemy 的 URL 对象 (2026-09-14 起), 所以先渲染成文本再逐字段
    核对 —— 直接断言对象相等会把「驱动名」也算进去, 反而验不出真正关心的东西.
    """
    dsn = postgres_dsn(
        {
            "CHARAGENT_CHECKPOINT_POSTGRES_DSN": "postgresql://u:p@db.example:5432/other",
            **PGSQL_ENV,
        }
    )

    fields = conninfo_to_dict(conninfo(dsn))
    assert fields["dbname"] == "other"
    assert fields["host"] == "db.example"


def test_postgres_dsn_reports_missing_variables():
    """共用变量缺了几个: 报错时列出缺的名字 (照着 .env 补就行)."""
    with pytest.raises(CheckpointConfigError) as excinfo:
        postgres_dsn({"PGSQL_HOST": "127.0.0.1"})

    message = str(excinfo.value)
    assert "PGSQL_USERNAME" in message
    assert "PGSQL_NAME" in message


def test_postgres_saver_requires_dsn():
    """直接构造 PG saver 却不给 dsn: 报错 (不等到连库时才炸)."""
    with pytest.raises(CheckpointConfigError):
        PostgresCheckpointSaver()
