"""alembic 迁移的真库验证 (标 `pg_db`, 默认排除).

**为什么不能只跑一次 `alembic upgrade head` 就算验过**: 那条命令在「表已经存在」
的库上会被 `IF NOT EXISTS` 放过, 于是「迁移脚本本身写没写对」根本没被验到. 要真
验, 得在一个**空 schema** 里从零跑一遍, 然后把库里的实际结构与代码里的表定义
逐列比一遍 —— 这才是「迁移建出来的表就是 schema.py 说的那张表」的证据.

隔离做法与 `test_db_store.py` 一致: 独立 schema (`charagent_alembic_test`),
跑完整个删掉. 开发库的 public 一个字节都不动.

版本表 (`charagent_migrations`) 也落在测试 schema 里 (通过 `version_table_schema`)
—— 否则它会写进 public, 而本机 public 里那张是开发环境的状态记录, 不该被测试改动.
**它兼作审计表** (ticket 24): 一行 = 当前 head, 外加 `name` / `history` 两列,
所以这里既验版本号, 也验「那一笔是谁什么时候记的」.
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
# 版本表 (ticket 24 起兼作审计表) 的名字, 理由见 alembic/env.py 的 VERSION_TABLE
VERSION_TABLE = "charagent_migrations"

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

    版本表是**迁移工具**的表, 不是我们的业务表 —— 断言「表齐全」时把它算进来就成了
    假阳性. 想验版本号的地方直接查它, 不用反射.
    """
    reflected = MetaData(schema=ALEMBIC_SCHEMA)
    reflected.reflect(bind=connection, schema=ALEMBIC_SCHEMA)
    reflected.remove(reflected.tables[f"{ALEMBIC_SCHEMA}.{VERSION_TABLE}"])
    return reflected


def test_upgrade_head_creates_every_table_on_an_empty_schema(migrated, _engine):
    """空库跑 `upgrade head`: 五张业务表齐全.

    这是一条关键验收 (「alembic 首次迁移在空库可执行」) —— 从零建库
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

    assert version == "0003_run_cost_columns"
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

    assert version == "0003_run_cost_columns"


def test_the_version_row_records_the_step_that_just_ran(migrated, _engine):
    """版本表那一行同时也是审计: 标题 + 走过来的每一笔, 都是真值.

    ticket 24 起版本表兼作审计表 (理由见 alembic/env.py 的 VERSION_TABLE):
    一行 = 当前 head, 外加 `name` (这一版的标题) 与 `history` (dict, 键 = 迁移编号,
    值 = 这一版最近一次成为当前版时的 `{from, at, by}`). 钩子写在 alembic 的**同一
    步**里, 所以时刻与执行者必定是真值 —— 不再有旧设计里「补记留空」那种半真半假
    的记录.

    2026-09-23 起这条链上有了**第二条**迁移 (ticket 20 的 0002), 于是从空库
    `upgrade head` 会走两步、钩子回调两次: 两个编号都在 `history` 里, 而
    `version_num` 与 `name` 是**最后停在**的那一版. 这正是「一行 = 当前 head,
    history = 一路怎么走过来的」那条设计在老库升级时该有的样子.

    2026-09-25 加上第三条 (ticket 28 的 0003) —— 它给运行行加了一列 (成本明细),
    并把金额那一列改成可空 (收尾算不出来时留 NULL, 与「花 0 元」分开).
    三步走完, 那条设计照旧成立: 迁移是**库该长成什么样**的历史, 每一版都算数.
    """
    with _engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT version_num, name,"
                " (SELECT count(*) FROM jsonb_object_keys(history)) AS versions,"
                " history -> version_num ->> 'from' AS from_rev,"
                " history -> version_num ->> 'at' AS at,"
                " history -> version_num ->> 'by' AS by"
                f" FROM {ALEMBIC_SCHEMA}.{VERSION_TABLE}"
            )
        ).one()

    assert row.version_num == "0003_run_cost_columns"
    assert row.name == "运行成本两列: 金额改成收尾写死 + 明细列", (
        "标题取的是脚本 docstring 的**首段** (alembic 的 `Script.doc` 就是这么切的)"
    )
    assert row.versions == 3, "空库到 head 走了三步 (0001 → 0002 → 0003)"
    assert row.from_rev == "0002_thread_management", "当前这一版是从 0002 上来的"
    assert row.at and row.by, "时刻与执行者都该是真值 (钩子写在同一步的事务里)"


def test_the_cost_column_has_no_default(migrated, _engine):
    """迁移跑完, `total_cost` **不带默认值** (默认值会改行为, 所以单独钉一条).

    为什么值得单独一条: `DEFAULT 0` 在那里的话, 任何漏写这一列的 INSERT 都会拿到 0
    —— 而 0 在这一列里的意思是「真的花了 0 元」, 与「还没算」是相反的结论. 这一条
    与下面那条零差异门分工不同: 那条比的是代码与库的整体, 这一条把那个**具体行为**
    写死 (即使哪天 `compare_server_default` 又被关掉, 它仍在).
    """
    from sqlalchemy import text

    with _engine.connect() as connection:
        default = connection.execute(
            text(
                "select column_default from information_schema.columns"
                " where table_schema = :s and table_name = 'charagent_runs'"
                " and column_name = 'total_cost'"
            ),
            {"s": ALEMBIC_SCHEMA},
        ).scalar()

    assert default is None, f"total_cost 不该有默认值, 实际: {default!r}"


def test_no_difference_between_code_and_migrated_schema(migrated, _engine):
    """迁移之后, alembic 自己比对「代码定义 vs 库里的结构」: 零差异.

    这条是**最强的那条一致性检查** —— 它逐表逐列比 (还会看索引、外键、注释),
    比手写对比更全面. 有差异时失败信息正好就是「差在哪儿」.

    顺带它也是「下次改 schema 该怎么做事」的示范: 改完 schema.py 跑
    `alembic revision --autogenerate`, 生成的脚本应当**只包含你这次真正改的东西**
    —— 若冒出一堆无关的差异, 说明某处已经漂移了.

    比的四样: 列与类型 / 可空 / 主键 / **默认值** (`compare_server_default`, 2026-09-25
    打开). 默认值那一项是补上的: 它此前只是没开, 于是「迁移少丢了一个 DEFAULT」在这道
    门下面**看不见**, 而那个默认值会改行为.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    with _engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                # 默认值也一起比 (2026-09-25 打开): 它此前只是**没开**, 于是
                # 「迁移少丢了一个 DEFAULT」这类漂移在这道门下面看不见 —— 而默认值
                # 恰恰会改行为 (漏写一列的 INSERT 拿到 0 = 「花了 0 元」)
                "compare_server_default": True,
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
