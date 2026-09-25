"""alembic 的运行入口: 每次 `alembic ...` 都会执行本文件, 由它把参数装好.

一句话理解: 这是 alembic 与**我们这个项目**之间的适配层 —— alembic 只管「按
版本号跑脚本」, 它不知道我们的表定义在哪儿、连接串从哪来, 都由本文件告诉它.

干四件事:

1. **连接串从环境变量来** (不写进 alembic.ini): 根 .env 的 PGSQL_*, 或
   CHARAGENT_DB_DSN 覆盖. 迁移是运维动作, 连接串该跟运行时同一套来源, 而不是在配置
   文件里再抄一份密码.
2. **告诉 alembic「代码侧该长什么样」** (`target_metadata`): autogenerate 靠它
   与库里的实际结构做 diff. 这里指的就是 `db/schema.py` 那份表定义 ——
   全项目唯一一份.
3. **提供两种跑法**: 在线 (连库执行) 与离线 (`--sql` 打印 SQL 不连库, 给 DBA
   审阅用).
4. **每跑完一步记一笔**: 往版本表那一行的 `name` / `history` 里写下「刚才是从哪一版
   到哪一版、什么时候、谁跑的」(见 record_migration).

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

# **版本表**. alembic 靠它记「这个库跑到哪一版了」: 一列 `version_num`, 一行 =
# 一个 head. 表由 alembic 自己在跑迁移之前建出来 (只带那一列, 主键名也由它定),
# 我们只往后补两列审计信息 (见 0001_core 与 record_migration).
#
# 名字带 charagent_ 前缀的理由: alembic 默认叫 `alembic_version` —— 一个不带项目
# 标识的通名. 本项目各子项目共用同一个 PG 库 (根 .env 的 PGSQL_*), 谁的迁移先跑,
# 这张表就是谁的: 另一个子项目再跑 alembic 会读到**我们**的版本号, 于是「表还没建」
# 却被判定为「已经到最新版」, 迁移直接跳过 —— 报错信息与真因 (版本表撞名) 毫无
# 关系, 极难排查. 业务表 (现在六张) 都带前缀的理由与此完全一致.
#
# **这张表兼作审计表** (ticket 24): 版本表天然只记当前 head, 而 alembic 把
# 「一行 = 一个 head」写死了 —— 它那张表**不可能**是「一行一条迁移」的累积形状,
# 多一行就会被当成多一个 head 直接报错. 既然累积与「当前」想说的是同一件事的
# 两种读法, 就不要两张必须时刻保持一致的冗余表: 在同一行上补 `name` (当前这一版
# 的标题) 与 `history` (每一版各是何时由谁成为当前版的), 一张表答完.
VERSION_TABLE = "charagent_migrations"

# 一步跑完: 改写标题 + 把这一版记进 history. `now()` 用库的时钟 (与这一步的写入
# 同一事务), 不用 Python 的 —— 审计的时刻该跟数据落在同一个时间源上.
#
# history 是 **dict, 键 = 迁移编号**, 值 = 这一版**最近一次成为当前版**时的
# `{from, at, by}`. 于是那一行自己就把三件事答完了: 现在在哪一版 (`version_num`)、
# 这一版何时由谁成为当前版 (`history -> version_num`)、一路经过哪几版 (键集合).
# 回退时改写的是**目标版那个键** —— 代价是「首次上线时刻」会被覆盖, 换来的是回退
# 也留痕 (只记首次的话, 一次 downgrade 之后这一行就在撒谎).
#
# 表名用 f-string 从 VERSION_TABLE 取, 不写死字面量: 名字只该在那一处定义, 而这条
# SQL 与下面那个守列判断若各写各的, 改名之后会出现「守门放行、UPDATE 命中 0 行、
# name/history 静默不写」这种最难查的组合.
_APPEND_HISTORY = text(
    f"update {VERSION_TABLE} set name = :name,"
    " history = coalesce(history, '{}'::jsonb) ||"
    " jsonb_build_object(cast(:revision as text), jsonb_build_object("
    "'from', cast(:from_revision as text),"
    " 'at', now(), 'by', cast(:applied_by as text)))"
    " where version_num = :revision"
)


def _applied_by() -> str | None:
    """执行者 `user@host`; 取不到就给 None.

    为什么取不到也不报错: 这是**审计信息**, 缺了它不该让一次迁移失败 —— 而这里抛
    异常会让整个迁移回滚 (一次登记失误拖垮一件正事, 那是本末倒置).
    """
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"[:128]
    except OSError:  # 极简容器里可能既没有用户名也没有主机名
        return None


def _audit_columns_ready(connection) -> bool:
    """版本表那两列审计信息在不在 (它们由 0001_core 补上).

    为什么需要这一判: `downgrade base` 的最后一步里, 0001_core 的 downgrade 先把
    这两列拆了, 而 alembic 的回调在那之后才跑 —— 不判一下就会撞「列不存在」, 让
    一次本该成功的回退失败在最后一步.

    只在 `current_schemas(false)` (即连接自己的 search_path) 里找: 版本表可能被按
    进别的 schema (测试就是这么干的), 全库同名的表不该被算进来.
    """
    return bool(
        connection.execute(
            text(
                "select count(*) = 2 from information_schema.columns"
                " where table_name = :table"
                " and column_name in ('name', 'history')"
                " and table_schema = any (current_schemas(false))"
            ),
            {"table": VERSION_TABLE},
        ).scalar()
    )


def record_migration(ctx, step, heads, run_args) -> None:
    """一步跑完了, 把这一笔写进版本表那一行 (由 alembic 在**同一事务**里回调).

    写两样东西: `name` = **走完之后**停在的那一版 (它的标题, 即脚本 docstring 的
    首行); `history` 里**这一版那个键**改成 `{from, at, by}` —— 升级写「从哪版上来
    的」, 回退写「从哪版退回来的」, 两个方向都落在同一个形状里. 于是这一行既是
    「现在在哪」, 也是「每一版各是什么时候由谁成为当前版的」.

    三处**安静跳过** (都是正常情形, 不是错误):
    - 离线模式没有连接可写;
    - `alembic stamp`: 只写版本号, 没有任何迁移动作 —— 记成「应用过」是假的;
    - 两列审计信息还不存在: 只有 `downgrade base` 的最后一步会走到 (0001_core 的
      downgrade 刚把它们拆掉), 而那一支之后 alembic 也把唯一那行删了 ——
      **`history` 连同「上过哪几版」的记录一起没, 要留档就先导出**.

    标题万一取不到 (脚本目录里查不到这条编号, 理论上不会: 它刚刚才被执行),
    宁缺勿编, 留空串.

    参数 `heads` / `run_args` 是 alembic 回调的固定形状 (按关键字传进来), 本函数
    不用它们 —— 名字不能改, 否则 TypeError.
    """
    if ctx.connection is None:  # 离线模式没有连接可写
        return
    if step.is_stamp:
        return
    if not _audit_columns_ready(ctx.connection):
        return
    # 走完之后停在哪一版: 升级时是刚应用的那条, 回退时是退到的那条, 都是 destination
    # —— 于是这一行永远说着「现在这里是哪一版」, history 的键也用它.
    destinations = step.destination_revision_ids
    if not destinations:
        return  # 退到 base: 没有「停在哪一版」可写
    revision_id = destinations[0]
    title = ""
    if ctx.script is not None:
        title = (ctx.script.get_revision(revision_id).doc or "")[:200]
    sources = step.source_revision_ids
    ctx.connection.execute(
        _APPEND_HISTORY,
        {
            "revision": revision_id,
            "name": title,
            "from_revision": sources[0] if sources else None,
            "applied_by": _applied_by(),
        },
    )


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
