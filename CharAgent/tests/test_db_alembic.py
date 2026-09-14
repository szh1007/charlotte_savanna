"""alembic 迁移的真库验证 (标 `pg_db`, 默认排除).

**为什么不能只跑一次 `alembic upgrade head` 就算验过**: 那条命令在「表已经存在」
的库上会被 `IF NOT EXISTS` 放过, 于是「迁移脚本本身写没写对」根本没被验到. 要真
验, 得在一个**空 schema** 里从零跑一遍, 然后把库里的实际结构与代码里的表定义
逐列比一遍 —— 这才是「迁移建出来的表就是 schema.py 说的那张表」的证据.

隔离做法与 `test_db_store.py` 一致: 独立 schema (`charagent_alembic_test`),
跑完整个删掉. 开发库的 public 一个字节都不动.

版本表 (charagent_alembic_version) 也落在测试 schema 里 (通过
`version_table_schema`) —— 否则它
会写进 public, 而本机 public 里那张是开发环境的状态记录, 不该被测试改动.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, pool, text
from sqlalchemy.engine import Connection

from CharAgent.db.schema import ALL_TABLES, metadata

pytestmark = pytest.mark.pg_db

ALEMBIC_SCHEMA = "charagent_alembic_test"
# alembic 的版本表名 (带项目前缀, 理由见 alembic/env.py 的 VERSION_TABLE)
VERSION_TABLE = "charagent_alembic_version"  # 带项目前缀, 理由见 alembic/env.py

# 仓库根目录 (本文件在 CharAgent/tests/ 下, 往上两级)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(url) -> Config:
    """造一份 alembic 配置.

    **不用 `Config("CharAgent/alembic.ini")`**: 那个类不展开 ini 里的 `%(here)s`
    (那是 alembic 自己的 ConfigParser 特性), 于是 `script_location` 会解析成一串
    带占位符的路径, 报「No 'script_location' key found」—— 错误信息完全指不到
    真因 (文件明明在那儿). 这里直接给绝对路径.
    """
    config = Config()
    config.set_main_option("script_location", str(_REPO_ROOT / "CharAgent" / "alembic"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    return config


@pytest.fixture(scope="module")
def _engine():
    """连到测试 schema 的同步引擎 (迁移本身是同步的, 不需要线程包装)."""
    from conftest import sqlalchemy_test_url

    url = sqlalchemy_test_url()
    engine = create_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"options": f"-csearch_path={ALEMBIC_SCHEMA}"},
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def migrated(_engine) -> Iterator[Config]:
    """空 schema + 跑完 `upgrade head` 的 alembic 配置.

    每个用例都从零开始: 删 schema → 重建 → 升级. 这样「首次迁移在空库可执行」
    这条验收每次都被真验一遍, 而不是只验一次就靠记忆.
    """
    admin = create_engine(_engine.url, poolclass=pool.NullPool)
    with admin.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {ALEMBIC_SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {ALEMBIC_SCHEMA}"))
    admin.dispose()

    config = _alembic_config(_engine.url)
    config.attributes["connection"] = _engine.connect()
    config.attributes["version_table_schema"] = ALEMBIC_SCHEMA
    command.upgrade(config, "head")
    try:
        yield config
    finally:
        config.attributes["connection"].close()
        admin = create_engine(_engine.url, poolclass=pool.NullPool)
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {ALEMBIC_SCHEMA} CASCADE"))
        admin.dispose()


def _reflect(connection: Connection) -> MetaData:
    """把测试 schema 里的实际结构读回来 (不含 alembic 自己的版本表).

    版本表是**迁移工具**的表, 不是我们的业务表 —— 断言「五张表齐全」时把它算进来
    就成了假阳性. 想验版本号的地方直接查它, 不用反射.
    """
    reflected = MetaData(schema=ALEMBIC_SCHEMA)
    reflected.reflect(bind=connection, schema=ALEMBIC_SCHEMA)
    reflected.remove(reflected.tables[f"{ALEMBIC_SCHEMA}.{VERSION_TABLE}"])
    return reflected


def test_upgrade_head_creates_every_table_on_an_empty_schema(migrated, _engine):
    """空库跑 `upgrade head`: 五张表齐全.

    这是 issue 08 验收的第 2 条 (「alembic 首次迁移在空库可执行」) —— 从零建库
    是每个新环境的第一件事, 它在半路报错的话后面什么都做不了.
    """
    with _engine.connect() as connection:
        found = set(_reflect(connection).tables)

    expected = {f"{ALEMBIC_SCHEMA}.{table.name}" for table in ALL_TABLES}
    assert found == expected, f"缺表: {expected - found} / 多出: {found - expected}"


def test_migrated_schema_matches_the_table_definitions(migrated, _engine):
    """迁移建出来的结构与 `db/schema.py` 逐列一致.

    比的是**列名 + 类型 + 可空 + 主键**, 这是「代码与库漂移」的直接防线:
    任何一边改了而另一边没跟上, 这条就红.

    为什么值得比到这个粒度: schema.py 是代码侧的唯一定义, 而库里的表是迁移建的
    —— 两者不一致时, 「写代码时以为的字段」与「实际能存的字段」就分家了, 而
    这种错误通常要等到某个字段写不进去才暴露.
    """
    from sqlalchemy.dialects import postgresql

    dialect = postgresql.dialect()
    mismatches: list[str] = []

    with _engine.connect() as connection:
        reflected = _reflect(connection)

    for table in ALL_TABLES:
        key = f"{ALEMBIC_SCHEMA}.{table.name}"
        actual = reflected.tables.get(key)
        if actual is None:
            mismatches.append(f"{table.name}: 库里没有这张表")
            continue

        for column in table.columns:
            mirror = actual.columns.get(column.name)
            if mirror is None:
                mismatches.append(f"{table.name}.{column.name}: 库里没有这一列")
                continue
            expected_type = column.type.compile(dialect)
            actual_type = mirror.type.compile(dialect)
            if expected_type != actual_type:
                mismatches.append(
                    f"{table.name}.{column.name}: 类型 {actual_type} != {expected_type}"
                )
            if column.nullable != mirror.nullable:
                mismatches.append(
                    f"{table.name}.{column.name}: 可空 {mirror.nullable} != "
                    f"{column.nullable}"
                )

        expected_pk = [c.name for c in table.primary_key.columns]
        actual_pk = [c.name for c in actual.primary_key.columns]
        if set(expected_pk) != set(actual_pk):
            mismatches.append(f"{table.name}: 主键 {actual_pk} != {expected_pk}")

    assert not mismatches, "迁移与表定义不一致:\n  " + "\n  ".join(mismatches)


def test_alembic_version_records_the_head_revision(migrated, _engine):
    """迁移跑完, 版本号记在版本表里 (落在测试 schema, 不污染 public)."""
    with _engine.connect() as connection:
        version = connection.execute(
            text(f"SELECT version_num FROM {ALEMBIC_SCHEMA}.{VERSION_TABLE}")
        ).scalar()
        # 版本表必须落在**测试 schema**, 而不是 public (开发环境的状态记录)
        test_schema_version = connection.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{ALEMBIC_SCHEMA}.{VERSION_TABLE}"},
        ).scalar()

    assert version == "0001_core"
    assert test_schema_version, (
        "版本表没落在测试 schema 里 (version_table_schema 没生效)"
    )


def test_upgrade_head_is_idempotent(migrated, _engine):
    """再跑一次 `upgrade head`: 什么都不做 (已经是最新), 也不报错.

    用户会重复跑这条命令 (部署脚本里常见) —— 第二次报错会让人以为环境坏了.
    """
    command.upgrade(migrated, "head")

    with _engine.connect() as connection:
        version = connection.execute(
            text(f"SELECT version_num FROM {ALEMBIC_SCHEMA}.{VERSION_TABLE}")
        ).scalar()

    assert version == "0001_core"


def test_no_difference_between_code_and_migrated_schema(migrated, _engine):
    """迁移之后, alembic 自己比对「代码定义 vs 库里的结构」: 零差异.

    这条是**最强的那条一致性检查** —— 它逐表逐列比 (还会看索引、外键、注释),
    比手写对比更全面. 有差异时失败信息正好就是「差在哪儿」.

    顺带它也是「下次改 schema 该怎么做事」的示范: 改完 schema.py 跑
    `alembic revision --autogenerate`, 生成的脚本应当**只包含你这次真正改的东西**
    —— 若冒出一堆无关的差异, 说明某处已经漂移了.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    with _engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "include_schemas": False,
                "version_table_schema": ALEMBIC_SCHEMA,
                # 版本表是 alembic 自己的, 不在我们的表定义里 —— 不排除掉的话,
                # 它会被报成「代码里多了一张表要删」(假阳性, 淹掉真实差异)
                "include_object": lambda obj, name, type_, *_: (
                    not (type_ == "table" and name == VERSION_TABLE)
                ),
            },
        )
        diffs = compare_metadata(context, metadata)

    assert not diffs, f"代码定义与迁移结果不一致: {diffs}"


def test_downgrade_removes_everything(migrated, _engine):
    """`downgrade base` 能把表全删掉 (迁移可回退).

    回退能力是迁移体系的另一半: 上线发现问题要能退回去. 这条也顺带确认
    downgrade 的删除顺序是对的 (有外键时顺序错了会报「被别的表引用」).
    """
    command.downgrade(migrated, "base")

    with _engine.connect() as connection:
        remaining = set(_reflect(connection).tables)

    assert not remaining, f"回退后还剩: {remaining}"
