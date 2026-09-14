"""按配置挑一个快照存储 (ADR-0002 说的「运行时配置切换」落在这一页).

一句话理解: 同一套框架, 今天用内存跑个测试、明天换 Redis 图快、后天换 Postgres
好翻历史 —— 换的是**配置**, 不是代码. 本文件就是把配置翻译成「造哪个 saver」的
那页翻译表.

配置项 (全部读环境变量; 第一个是选后端, 其余的只在对应后端才需要):

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| CHECKPOINT_BACKEND | memory | memory / redis / postgres |
| CHECKPOINT_KEY_PREFIX | charagent | Redis 键前缀 (键名 `{前缀}:ckpt:{会话}`) |
| CHECKPOINT_TTL_SECONDS | 不过期 | Redis 快照存活秒数 |
| CHECKPOINT_REDIS_MODE | history | Redis 模式: history (全历史) / latest (只留最新) |
| CHECKPOINT_REDIS_MAX_FRAMES | 不裁剪 | history 模式最多留几帧 (裁掉最老的) |
| CHECKPOINT_REDIS_URL | REDIS_URL 或本机 6379 | Redis 连接串 |
| CHECKPOINT_POSTGRES_DSN | 由 PGSQL_* 拼 | Postgres 连接串 |

为什么 Postgres 可以用 PGSQL_* 兜底: 根 .env 里那几个变量 (PGSQL_USERNAME /
PGSQL_PASSWORD / PGSQL_HOST / PGSQL_PORT / PGSQL_NAME) 是本项目各子项目共用的
数据库配置, 快照就存在同一个库里, 没必要再抄一遍; 想单独指到别的库时用
CHECKPOINT_POSTGRES_DSN 覆盖.

拼连接串用 psycopg 自己的 make_conninfo (而不是手写 f-string): 密码里万一有
`@` `:` `/` 这类字符, 手拼会把连接串拼坏 —— 交给库去转义.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from psycopg.conninfo import make_conninfo

from CharAgent.checkpoint.base import CheckpointSaver
from CharAgent.checkpoint.memory import InMemoryCheckpointSaver
from CharAgent.checkpoint.postgres import PostgresCheckpointSaver
from CharAgent.checkpoint.redis import (
    DEFAULT_KEY_PREFIX,
    DEFAULT_URL,
    MODE_HISTORY,
    RedisCheckpointSaver,
)
from CharAgent.checkpoint.utils.errors import CheckpointConfigError

BACKEND_MEMORY = "memory"
BACKEND_REDIS = "redis"
BACKEND_POSTGRES = "postgres"
BACKEND_NAMES = (BACKEND_MEMORY, BACKEND_REDIS, BACKEND_POSTGRES)

ENV_BACKEND = "CHECKPOINT_BACKEND"
ENV_KEY_PREFIX = "CHECKPOINT_KEY_PREFIX"
ENV_TTL_SECONDS = "CHECKPOINT_TTL_SECONDS"
ENV_REDIS_MODE = "CHECKPOINT_REDIS_MODE"
ENV_REDIS_MAX_FRAMES = "CHECKPOINT_REDIS_MAX_FRAMES"
ENV_REDIS_URL = "CHECKPOINT_REDIS_URL"
ENV_POSTGRES_DSN = "CHECKPOINT_POSTGRES_DSN"

# Postgres 连接信息: 优先专用的 CHECKPOINT_POSTGRES_DSN, 其次用这几个拼
_PGSQL_KEYS = (
    "PGSQL_USERNAME",
    "PGSQL_PASSWORD",
    "PGSQL_HOST",
    "PGSQL_PORT",
    "PGSQL_NAME",
)


def checkpoint_saver_from_env(
    env: Mapping[str, str] | None = None,
) -> CheckpointSaver:
    """按环境变量挑一个存储实现 (选谁看 CHECKPOINT_BACKEND, 默认内存版).

    Args:
        env: 环境变量表 (默认读 os.environ; 测试传一个字典就不必改真环境).

    Returns:
        CheckpointSaver: 还没连库的 saver (Redis / Postgres 都是懒连接, 第一次
        存取才连).

    Raises:
        CheckpointConfigError: 后端名不认识, 或该后端缺连接信息.
    """
    source = os.environ if env is None else env
    backend = (source.get(ENV_BACKEND) or BACKEND_MEMORY).strip().lower()
    return build_saver(backend, source)


def build_saver(backend: str, env: Mapping[str, str] | None = None) -> CheckpointSaver:
    """按名字造 saver (显式指定后端时用它; 名字大小写与首尾空格都宽容).

    Raises:
        CheckpointConfigError: 名字不在 memory / redis / postgres 里.
    """
    source = os.environ if env is None else env
    name = (backend or "").strip().lower()
    if name == BACKEND_MEMORY:
        return InMemoryCheckpointSaver()
    if name == BACKEND_REDIS:
        return RedisCheckpointSaver(
            url=(source.get(ENV_REDIS_URL) or source.get("REDIS_URL") or DEFAULT_URL),
            mode=source.get(ENV_REDIS_MODE) or MODE_HISTORY,
            ttl_seconds=_read_optional_int(source, ENV_TTL_SECONDS),
            max_frames=_read_optional_int(source, ENV_REDIS_MAX_FRAMES),
            key_prefix=source.get(ENV_KEY_PREFIX) or DEFAULT_KEY_PREFIX,
        )
    if name == BACKEND_POSTGRES:
        return PostgresCheckpointSaver(dsn=postgres_dsn(source))
    raise CheckpointConfigError(
        f"未知的 checkpoint 后端 {backend!r}, 可选: {', '.join(BACKEND_NAMES)}"
    )


def _read_optional_int(source: Mapping[str, str], key: str) -> int | None:
    """读一个可选的整数配置: 空值表示不设 (None); 不是整数就报错 (别猜).

    取值范围的正负由 saver 那边判 (TTL 与 max_frames 都要求正整数), 这里只负责
    「有没有给」和「是不是整数」两件事.
    """
    raw = source.get(key)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        raise CheckpointConfigError(f"{key} 应当是整数, 实际: {raw!r}") from None


def postgres_dsn(env: Mapping[str, str] | None = None) -> str:
    """拼 Postgres 连接串: 先用专用变量, 否则用共用的 PGSQL_* 拼.

    公开的理由: 除了造 saver, 还有些场合需要**连接串本身** —— 手工连库排查、
    跑 alembic 迁移、测试里直接查表对账. 与其让各处重复抄一遍拼法, 不如共用
    这一处 (顺便统一了转义规则, 见模块 docstring).

    Args:
        env: 环境变量表 (默认读 os.environ).

    Raises:
        CheckpointConfigError: 两种来源都不完整 (报错时列出缺哪些变量).
    """
    source = os.environ if env is None else env
    explicit = (source.get(ENV_POSTGRES_DSN) or "").strip()
    if explicit:
        return explicit
    values = {key: (source.get(key) or "").strip() for key in _PGSQL_KEYS}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise CheckpointConfigError(
            f"没拿到 Postgres 连接信息: 请设 {ENV_POSTGRES_DSN}, 或补齐 "
            f"{', '.join(missing)} (与根 .env 的 PGSQL_* 同名)"
        )
    return make_conninfo(
        user=values["PGSQL_USERNAME"],
        password=values["PGSQL_PASSWORD"],
        host=values["PGSQL_HOST"],
        port=values["PGSQL_PORT"],
        dbname=values["PGSQL_NAME"],
    )
