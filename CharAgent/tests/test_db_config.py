"""连接配置的自检 (不需要数据库): 环境变量 → 连接串.

**为什么这批用例重要**: `sqlalchemy_url()` 是 alembic 迁移与运行时**唯一**的连接串
来源 —— 它配错了不会报错, 只会连到别的库上去 (或者在部署时才炸). 而它此前一例
未测 (2026-09-14 覆盖审计发现, 当时它整文件零覆盖).

三件事:
- `sqlalchemy_url()` 的三种来源与两条报错
- `echo_enabled()` 的真值表 (它只影响日志噪音, 写错了不该报错)
- **与 `checkpoint/config.py` 的结果一致** —— 两处各自从 `PGSQL_*` 拼 (不共用是
  为了避开循环 import, 见 `db/config.py` 的模块 docstring), 那两条路径必须给出
  同一个库, 否则「快照存在 A 库、业务数据在 B 库」这种错极难发现.
"""

from __future__ import annotations

import pytest
from sqlalchemy import URL

from CharAgent.checkpoint.config import postgres_dsn
from CharAgent.db.config import (
    ENV_DSN,
    ENV_ECHO,
    DataConfigError,
    echo_enabled,
    sqlalchemy_url,
)

PGSQL_ENV = {
    "PGSQL_USERNAME": "charlotte",
    "PGSQL_PASSWORD": "pwd",
    "PGSQL_HOST": "127.0.0.1",
    "PGSQL_PORT": "5432",
    "PGSQL_NAME": "charlotte",
}


def _fields(url) -> dict:
    """把 URL 拆成可比对的字段 (密码取明文, 驱动名单独看)."""
    return {
        "driver": url.drivername,
        "user": url.username,
        "password": url.password,
        "host": url.host,
        "port": url.port,
        "db": url.database,
    }


# --- sqlalchemy_url: 三种来源 -------------------------------------------------


def test_builds_from_shared_env_with_psycopg_driver():
    """没给专用 DSN: 从共用的 PGSQL_* 拼, **驱动名必须是 psycopg**.

    驱动名错了会去找 psycopg2 (本项目没装), 报「找不到驱动」—— 而那个报错不会
    提示「你该写 +psycopg」.
    """
    fields = _fields(sqlalchemy_url(PGSQL_ENV))

    assert fields == {
        "driver": "postgresql+psycopg",
        "user": "charlotte",
        "password": "pwd",
        "host": "127.0.0.1",
        "port": 5432,
        "db": "charlotte",
    }


def test_explicit_dsn_with_driver_is_passed_through():
    """专用 DSN 已经写了驱动名: 原样用 (不重复加前缀)."""
    url = sqlalchemy_url({ENV_DSN: "postgresql+psycopg://u:p@db.example:5432/other"})

    assert _fields(url)["driver"] == "postgresql+psycopg"
    assert _fields(url)["host"] == "db.example"
    assert _fields(url)["db"] == "other"


def test_explicit_plain_url_gets_the_driver_added():
    """专用 DSN 是普通 URL: 补上 `+psycopg` (否则 SQLAlchemy 退默认驱动)."""
    url = sqlalchemy_url({ENV_DSN: "postgresql://u:p@db.example:5432/other"})

    assert url.drivername == "postgresql+psycopg"


def test_postgres_scheme_is_also_accepted():
    """`postgres://` 这种老写法也认 (有些托管服务的连接串就是这个)."""
    url = sqlalchemy_url({ENV_DSN: "postgres://u:p@db.example:5432/other"})

    assert url.drivername == "postgresql+psycopg"
    assert url.database == "other"


def test_unknown_scheme_is_rejected():
    """认不出的写法当场报错, 并把那个协议名打出来 (别让人去猜).

    不猜是有意的: 猜错就是连到别的库或另一个数据库产品上.
    """
    with pytest.raises(DataConfigError) as excinfo:
        sqlalchemy_url({ENV_DSN: "mysql://u:p@h/d"})

    assert "mysql" in str(excinfo.value)


def test_missing_variables_are_named_in_the_error():
    """共用变量缺几个就点名几个 (照着 .env 补即可)."""
    with pytest.raises(DataConfigError) as excinfo:
        sqlalchemy_url({"PGSQL_HOST": "127.0.0.1"})

    message = str(excinfo.value)
    assert "PGSQL_USERNAME" in message
    assert "PGSQL_NAME" in message
    assert "PGSQL_HOST" not in message  # 给了的那个不该被点名


def test_quotes_in_env_values_are_stripped():
    """.env 里常写 `PGSQL_USERNAME="charlotte"` —— 引号是给 shell 看的分隔符.

    不剥的话用户名会变成 `"charlotte"` (带引号), 连库时报「角色不存在」, 而报错
    里的名字看着就像对的那个 —— 极难发现.
    """
    env = {key: f'"{value}"' for key, value in PGSQL_ENV.items()}

    fields = _fields(sqlalchemy_url(env))

    assert fields["user"] == "charlotte"
    assert fields["db"] == "charlotte"


def test_special_characters_in_password_are_escaped():
    """密码里的 `@` `:` `/` 交给 URL.create 转义, 不会把连接串拼坏."""
    url = sqlalchemy_url({**PGSQL_ENV, "PGSQL_PASSWORD": "p@ss:w/ord"})

    # 取回来必须还是原值 (说明转义与还原是对的)
    assert url.password == "p@ss:w/ord"


# --- echo_enabled: 真值表 -----------------------------------------------------


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "Yes", "on", " on "])
def test_echo_truthy_values(raw):
    """认 1/true/yes/on, 大小写与首尾空格都宽容 (手写 .env 最容易多敲空格)."""
    assert echo_enabled({ENV_ECHO: raw}) is True


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "随便写点什么"])
def test_echo_falsy_values(raw):
    """其余一律按「关」处理 —— 这个开关只影响日志噪音, 不值得为它报错."""
    assert echo_enabled({ENV_ECHO: raw}) is False


def test_echo_defaults_to_off_when_unset():
    """没配就是不打印 SQL (默认安静)."""
    assert echo_enabled({}) is False


# --- 两条路径必须指向同一个库 ------------------------------------------------


def test_db_and_checkpoint_agree_on_the_same_environment():
    """同样的环境变量, `db` 与 `checkpoint` 拼出来的必须是**同一个库**.

    两处各写一份拼装逻辑 (避循环 import 的代价), 于是必须有用例盯着它们不走偏
    —— 分叉的后果是「快照存在 A 库、业务数据在 B 库」, 排查时两边都看着正常.

    (`checkpoint` 那边返回的是 URL 对象, 字段直接可比.)
    """
    db_fields = _fields(sqlalchemy_url(PGSQL_ENV))
    checkpoint_fields = _fields(postgres_dsn(PGSQL_ENV))

    # 驱动名由各自决定, 其余字段必须逐一相同
    assert db_fields["driver"] == checkpoint_fields["driver"]
    assert {k: v for k, v in db_fields.items() if k != "driver"} == {
        k: v for k, v in checkpoint_fields.items() if k != "driver"
    }


def test_both_paths_report_missing_variables():
    """两边缺变量时都要报错 (而不是一边悄悄用默认值连上本机).

    两边都用 `os.environ` 兜底 (无参时), 所以这里显式传一个空字典把兜底按掉,
    否则用例的结果取决于跑测试的机器有没有配 PGSQL_*.
    """
    # db 那边抛自己的 DataConfigError; checkpoint 那边抛 CheckpointConfigError
    # (两处各有一族异常, 这里只关心「都报错且都点名了 PGSQL_*」)
    for build in (sqlalchemy_url, postgres_dsn):
        with pytest.raises(Exception) as excinfo:
            build({})
        assert "PGSQL" in str(excinfo.value)
    with pytest.raises(DataConfigError):
        sqlalchemy_url({})


@pytest.mark.parametrize(
    "env",
    [
        PGSQL_ENV,
        {ENV_DSN: "postgresql+psycopg://u:p@h/d"},
        {ENV_DSN: "postgresql://u:p@h/d"},
        {ENV_DSN: "postgres://u:p@h/d"},
    ],
    ids=["from-pgsql-env", "explicit-with-driver", "explicit-plain", "postgres-scheme"],
)
def test_every_path_returns_a_url_object(env):
    """三条来源**统一**返回 URL 对象 (不是一个函数两种返回类型).

    这条是 2026-09-14 修的: 此前显式 DSN 那条路原样返回字符串, 调用方得同时防
    两种形态 (`url.drivername` 在字符串上直接 AttributeError).
    """
    assert isinstance(sqlalchemy_url(env), URL)
