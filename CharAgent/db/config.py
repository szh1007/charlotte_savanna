"""db 包的连接配置: 「连哪个库」只在这一页说 (difficulties #64).

一句话理解: 这个文件把环境变量翻译成一条**能用的连接串**, 于是「换个库」改的是
配置而不是代码 —— 本机开发、连测试库、部署到别处, 都只是换个环境变量的值.

配置项 (全部读环境变量):

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| MODELS_DSN | 由 PGSQL_* 拼 | 专用连接串 (想指到别的库时用它覆盖) |
| MODELS_ECHO | 关 | 是否把执行的 SQL 打到日志 (排查时开, 平时关) |
| `PGSQL_USERNAME` / `PGSQL_PASSWORD` / `PGSQL_HOST` / `PGSQL_PORT` /
  `PGSQL_NAME` | 无 | 共用库配置 (与根 `.env` 同名) |

**为什么不复用 `checkpoint/config.py` 的 `postgres_dsn`**: 因为会绕成循环
import —— `checkpoint/postgres.py` 要 import `db.database` (快照存储用 SQLAlchemy
实现), 本文件若再反向 import checkpoint 的配置, 两边就互指了. 于是各自从
`PGSQL_*` 拼一次, 规则保持一致 (缺哪个变量就点名哪个; 密码里的 `@` `:` 交给
`URL.create` 转义, 不手拼 f-string).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy import URL
from sqlalchemy.engine import make_url

from CharAgent.db.errors import DataConfigError

ENV_DSN = "MODELS_DSN"
ENV_ECHO = "MODELS_ECHO"

# 共用库的几个变量 (与根 .env 同名; 只在这里列一次, 拼 URL 与报错都用它)
_PGSQL_KEYS = (
    "PGSQL_USERNAME",
    "PGSQL_PASSWORD",
    "PGSQL_HOST",
    "PGSQL_PORT",
    "PGSQL_NAME",
)

# 布尔配置认得的「真」写法 (跟着 .env 的习惯走: 1/true/yes/on)
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _pgsql_values(source: Mapping[str, str]) -> dict[str, str]:
    """读共用库的那几个变量 (去掉首尾空白与 .env 里的引号).

    .env 里常见写 `PGSQL_USERNAME="charlotte"` —— 引号是给 shell 看的分隔符,
    值本身不带它们, 所以这里统一剥掉.
    """
    return {key: (source.get(key) or "").strip().strip('"') for key in _PGSQL_KEYS}


def sqlalchemy_url(env: Mapping[str, str] | None = None) -> URL:
    """拿到 SQLAlchemy 能用的 URL.

    **为什么要单独一个函数** (而不是直接把 `db_dsn()` 的结果丢给
    `create_engine`): 驱动名. SQLAlchemy 从 URL 的协议名里判断用哪个驱动 ——
    `postgresql://` 会让它去找 **psycopg2** (本项目装的是 psycopg3, 直接报
    「找不到驱动」). URL 形式要写成 `postgresql+psycopg://`.

    **但 `postgres_dsn()` 给的其实不是 URL**: 它是 psycopg 的**关键字形式**
    (`user=... password=... host=...`). 这种写法 SQLAlchemy 也认, 但里面没有
    协议名, 于是会退回到它自己的默认驱动 —— 同样不是 psycopg3. 所以交给
    `URL.create()` 显式指定驱动 (`postgresql+psycopg`); 它顺带把密码里的特殊
    字符也转义好 (`@` `:` 直接写进 URL 会把它拼坏).

    三种来源各走各的路:
    1. `MODELS_DSN` 已带驱动名 (`postgresql+psycopg://...`) → 原样用
    2. `MODELS_DSN` 是普通 URL (`postgresql://...`) → 补上驱动名
    3. 没有 `MODELS_DSN` → 从 `PGSQL_*` 逐项拼 URL (走 `URL.create`)

    Args:
        env: 环境变量表 (默认读 os.environ).

    Returns:
        URL: SQLAlchemy 的 URL 对象 —— **三条路径统一**返回它 (2026-09-14 修):
            此前显式 DSN 那条路会原样返回字符串, 于是调用方得同时防两种形态
            (`url.drivername` 在字符串上会 AttributeError). `create_engine` 两种
            都收, 但**返回类型统一**才对得住「一个函数一个契约.

    Raises:
        DataConfigError: `MODELS_DSN` 给了个认不出的写法.
    """
    source = os.environ if env is None else env
    explicit = (source.get(ENV_DSN) or "").strip()
    if explicit:
        scheme = explicit.split("://", 1)[0] if "://" in explicit else ""
        if scheme:
            if "+" in scheme:
                # 已经带了驱动名: 过一遍 make_url 让返回值统一是 URL 对象
                return make_url(explicit)
            if scheme in ("postgresql", "postgres"):
                return make_url("postgresql+psycopg://" + explicit.split("://", 1)[1])
        raise DataConfigError(
            f"不认识的连接串写法 (需要 postgresql:// 开头): {scheme or explicit[:24]!r}"
        )
    # 没给专用 DSN: 逐项拼. 用 URL.create 而不是字符串拼接 —— 密码里的
    # 特殊字符交给库去转义 (与 checkpoint 那边同一个理由)
    values = _pgsql_values(source)
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise DataConfigError(
            f"没拿到 Postgres 连接信息: 请设 {ENV_DSN}, 或补齐 "
            f"{', '.join(missing)} (与根 .env 的 PGSQL_* 同名)"
        )
    return URL.create(
        "postgresql+psycopg",
        username=values["PGSQL_USERNAME"],
        password=values["PGSQL_PASSWORD"],
        host=values["PGSQL_HOST"],
        port=int(values["PGSQL_PORT"]),
        database=values["PGSQL_NAME"],
    )


def echo_enabled(env: Mapping[str, str] | None = None) -> bool:
    """是否把 SQL 打到日志 (MODELS_ECHO).

    认 1/true/yes/on (不分大小写); 其余值一律按「关」处理 —— 这个开关只影响
    日志噪音, 不值得为「写了个看不懂的值」报错.
    """
    source = os.environ if env is None else env
    return (source.get(ENV_ECHO) or "").strip().lower() in _TRUTHY
