"""alembic 的运行入口: 每次 `alembic ...` 都会执行本文件, 由它把参数装好.

一句话理解: 这是 alembic 与**我们这个项目**之间的适配层 —— alembic 只管「按
版本号跑脚本」, 它不知道我们的表定义在哪儿、连接串从哪来, 都由本文件告诉它.

干三件事:

1. **连接串从环境变量来** (不写进 alembic.ini): 根 .env 的 PGSQL_*, 或
   CHARAGENT_DB_DSN 覆盖. 迁移是运维动作, 连接串该跟运行时同一套来源, 而不是在配置
   文件里再抄一份密码.
2. **告诉 alembic「代码侧该长什么样」** (`target_metadata`): autogenerate 靠它
   与库里的实际结构做 diff. 这里指的就是 `db/schema.py` 那份表定义 ——
   全项目唯一一份.
3. **提供两种跑法**: 在线 (连库执行) 与离线 (`--sql` 打印 SQL 不连库, 给 DBA
   审阅用).

用**同步**引擎 (而不是 async): 迁移是命令行动作, 没有事件循环在跑, 同步正是
它要的 (同步/异步的取舍见 db/database.py 的模块 docstring).
"""

from __future__ import annotations

import getpass
import socket
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool, text

from CharAgent.db.config import echo_enabled, sqlalchemy_url
from CharAgent.db.schema import metadata

# **先把根 .env 读进环境变量**: alembic 是个独立的命令行工具, 它不会自动加载
# .env (应用与测试各自加载过了, 迁移脚本却没有这一步) —— 少了它, 上面那句
# `sqlalchemy_url()` 会因为「一个变量都没有」而报错, 而报错信息看起来像是
# 用户没配 .env, 实际只是没人读它.
#
# override=False: 已经存在于真环境里的变量优先 (CI / 生产靠这个覆盖 .env).
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

# alembic 的配置对象 (就是 alembic.ini 读出来的那份)
config = context.config

# 日志按 alembic.ini 的 [loggers] 配 (迁移过程中的 INFO 会打出来)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 「代码侧该长什么样」的基准 —— autogenerate 拿它与库里的实际结构比对.
# 它来自 db/schema.py: 表定义只有那一处, 这里只是引用.
target_metadata = metadata

# **版本表也要带 charagent_ 前缀**. alembic 默认叫 `alembic_version` —— 一个
# 不带项目标识的通名. 本项目各子项目共用同一个 PG 库 (根 .env 的 PGSQL_*), 谁
# 的迁移先跑, 这张表就是谁的: 另一个子项目再跑 alembic 会读到**我们**的版本号,
# 于是「表还没建」却被判定为「已经到最新版」, 迁移直接跳过 —— 报错信息与真因
# (版本表撞名) 毫无关系, 极难排查. 五张业务表都带前缀的理由与此完全一致.
VERSION_TABLE = "charagent_alembic_version"


# --- 迁移审计: 每应用一条迁移往 charagent_migrations 追加一行 ----------------
#
# 为什么需要它: `charagent_alembic_version` 只记**当前 head** (alembic 靠那一行
# 决定还该跑哪些迁移, 多行会被当成分叉的 head) —— 于是「这张库什么时候上的哪条
# 迁移」在库里查不到. 审计表补上这件事, 定义在 db/schema.py 的 migrations.
AUDIT_TABLE = "charagent_migrations"

_INSERT_APPLIED = text(
    "insert into charagent_migrations (revision, name, applied_at, applied_by)"
    " values (:revision, :name, now(), :applied_by)"
    " on conflict (revision) do nothing"
)
_DELETE_APPLIED = text("delete from charagent_migrations where revision = :revision")


def _applied_by() -> str | None:
    """执行者 `user@host`; 取不到就给 None.

    为什么取不到也不报错: 这是**审计信息**, 缺了它不该让一次迁移失败 —— 而这里抛
    异常会让整个迁移回滚 (一次登记失误拖垮一件正事, 那是本末倒置).
    """
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"[:128]
    except OSError:  # 极简容器里可能既没有用户名也没有主机名
        return None


def _audit_table_exists(connection) -> bool:
    """审计表在不在 (它由 0003 建起来, 那之前的几步必然不在)."""
    return bool(
        connection.execute(
            text("select to_regclass(:name) is not null"), {"name": AUDIT_TABLE}
        ).scalar()
    )


def record_migration(ctx, step, heads, run_args) -> None:
    """一条迁移跑完了, 往审计表记一笔 (由 alembic 在**同一事务**里回调).

    应用就插一行、回退就删一行 —— 两个方向都记, 表里那一份才始终等于「当前实际
    应用了哪些迁移」; 只插不删的话, 一次 downgrade 之后表就在撒谎.

    三种**安静跳过** (都是正常情形, 不是错误):
    - 审计表还不存在: 0001 / 0002 跑的时候它必然不在 (那两条由 0003 补记, 见
      0003 的 docstring); 0003 的 downgrade 把表自己删了, 也会走到这一支;
    - `alembic stamp`: 只写版本号, 没有任何迁移动作 —— 记成「应用过」是假的;
    - 拿不到编号 (`up_revision_id` 为空): 记不了就不记半行.

    标题取自脚本 docstring 的首行 (`Script.doc`), 与 `alembic history` 印的是同一句;
    万一脚本目录里查不到这条编号 (理论上不会: 它刚刚才被执行), 宁缺勿编, 留空串.

    参数 `heads` / `run_args` 是 alembic 回调的固定形状 (按关键字传进来), 本函数不用
    它们 —— 名字不能改, 否则 TypeError.
    """
    if ctx.connection is None:  # 离线模式没有连接可写
        return
    if step.is_stamp:
        return
    if not _audit_table_exists(ctx.connection):
        return
    revision_id = step.up_revision_id
    if not revision_id:
        return
    if step.is_upgrade:
        title = ""
        if ctx.script is not None:
            title = (ctx.script.get_revision(revision_id).doc or "")[:200]
        ctx.connection.execute(
            _INSERT_APPLIED,
            {
                "revision": revision_id,
                "name": title,
                "applied_by": _applied_by(),
            },
        )
    else:
        ctx.connection.execute(_DELETE_APPLIED, {"revision": revision_id})


def _database_url() -> str:
    """拼接库连接串 (环境变量 → SQLAlchemy URL 文本).

    注意把密码**还原**成明文: SQLAlchemy 的 `str(URL)` 默认会把密码渲染成三个
    星号 (防日志泄露, 是好设计), 但 `create_engine` 拿到带星号的串会真的用
    `***` 去连库, 报「密码认证失败」—— 这个坑踩过一次就很难想到.
    """
    return sqlalchemy_url().render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    """离线模式 (`alembic upgrade head --sql`): 只生成 SQL 文本, 不连库.

    给「DBA 要先审一遍 SQL 才能上生产」的场景用. 生成的 SQL 打到标准输出.
    """
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式 (默认): 连上库, 按版本号执行迁移脚本.

    **用同步引擎**: 迁移是命令行动作, 没有事件循环要照顾 (见模块 docstring).
    与运行时那套「同步引擎 + asyncio.to_thread」是同一个驱动, 只是这里没必要
    绕线程 —— 阻塞的正是它自己.
    """
    # 调用方可以塞一个现成连接进来 (`config.attributes["connection"]`) ——
    # 用例就是这么在隔离 schema 里跑迁移的. 没塞就自己建引擎 (命令行场景).
    injected = config.attributes.get("connection")
    if injected is not None:
        _run_with_connection(injected)
        return

    engine = create_engine(
        _database_url(),
        echo=echo_enabled(),
        # NullPool: 迁移跑完就退出进程, 留着连接池没有意义 (还会让进程多等一会儿)
        poolclass=pool.NullPool,
    )

    with engine.connect() as connection:
        _run_with_connection(connection)


def _run_with_connection(connection) -> None:
    """在一份已有连接上跑迁移.

    `version_table_schema` 可以经 `config.attributes` 指定 —— 用例把版本表也按在
    测试 schema 里, 免得写进开发库的 public (那是环境状态, 不该被测试改).
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 列类型大小写/比较方式交给 alembic 默认的比对器 —— 它对 Postgres 的类型
        # 映射已经处理好了 (VARCHAR 长度、TIMESTAMPTZ、JSONB 等)
        compare_type=True,
        version_table=VERSION_TABLE,
        version_table_schema=config.attributes.get("version_table_schema"),
        # 每跑完一条迁移记一行审计 (见 record_migration). 只挂在线模式: 离线模式
        # 没有连接可写 (生成的 SQL 里也不该掺审计插入 —— DBA 审的是表结构变更)
        on_version_apply=record_migration,
    )

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
