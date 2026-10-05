"""SQL 护栏判据 (纯函数): 白名单 / LIMIT 补齐与收紧 / 留痕.

这份实现从 C15 的 `app/eval/guard.py` 搬来并升级成**生产链路的执行咽喉**
(`DwMysqlRepository` 调它), 所以用例也一并升级: 除了「拦得住写操作」, 还要钉住
「合法语句不被误伤」与「LIMIT 的补齐/收紧只动数字, 不重写语句」.

四个真踩过的坑留了回归用例 (前两个来自 C15, 后两个来自 C16 的评审):

1. 掩码 (屏蔽字面量/注释) 只能用于**扫描** —— 拿掩码后的文本拼要执行的 SQL 会把
   `WHERE province = '广东省'` 的取值整段抹掉 (gold 自检当场抓住 12 道);
2. 块注释那段正则曾**漏了 `|`**, 于是 `/* … */` 从来没被屏蔽过;
3. **`/*! … */` 是可执行注释** (MySQL 会执行里面的内容), 而 `--x` 不是注释
   (MySQL 要求 `--` 后跟空白) —— 把它们当惰性注释, 都是绕过白名单的通道;
4. **外层派生表包裹会弄坏合法 SQL**: 两表同名列 (`SELECT f.date_id, d.date_id …`)
   包进去直接报 1060 Duplicate column name —— 所以上限改成在文本层面补/收。
"""

from __future__ import annotations

import pytest

from app.core.sql_guard import (
    DEFAULT_LIMIT,
    LIMIT_CAP,
    GuardNote,
    SqlGuardLimits,
    enforce_limit,
    own_limit_value,
    own_limits,
    readonly_violation,
    strip_trailing_semicolon,
)

# ---------------------------------------------------------------------------
# 白名单: 放行的形态
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(order_amount) FROM fact_order",
        "select 1",
        "  SELECT * FROM dim_region  ",
        "SELECT * FROM dim_region;",  # 单个收尾分号是常见写法, 必须放行
        "WITH t AS (SELECT 1 AS a) SELECT a FROM t",  # CTE 是最容易被误杀的合法形态
        # 关键词黑名单不能按「子串」判: created_at 里含 create, 'drop' 字面量含 drop
        "SELECT created_at FROM t",
        "SELECT * FROM dim_region WHERE region_name = 'drop table'",
        "SELECT 1 -- DROP TABLE fact_order",  # 真注释里的写操作不算数
        "SELECT /* UPDATE 是注释 */ 1",
        # 可执行注释会被扫描, 但里面是只读内容时照样放行 (挡的是写操作, 不是注释本身)
        "SELECT 1 /*!50000 UNION SELECT 2 */",
        # REPLACE 同时是只读字符串函数 —— 放进禁词表会把这种正常查询误杀
        "SELECT REPLACE(region_name, '省', '') FROM dim_region",
    ],
)
def test_allows_readonly_queries(sql: str) -> None:
    assert readonly_violation(sql) is None


# ---------------------------------------------------------------------------
# 白名单: 拦下的形态 + 拒绝理由要是「可操作文本」
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "needle"),
    [
        ("DROP TABLE fact_order", "DROP"),
        ("TRUNCATE TABLE fact_order", "TRUNCATE"),
        ("DELETE FROM fact_order", "DELETE"),
        ("UPDATE fact_order SET order_amount = 0", "UPDATE"),
        ("INSERT INTO dim_region VALUES (1)", "INSERT"),
        ("ALTER TABLE fact_order ADD COLUMN x INT", "ALTER"),
        ("CREATE TABLE t (i INT)", "CREATE"),
        ("SELECT * FROM fact_order INTO OUTFILE '/tmp/x'", "OUTFILE"),
        ("SELECT 1; DROP TABLE fact_order", ";"),
        ("SELECT 1 ; SELECT 2", ";"),
        ("EXPLAIN SELECT 1", "SELECT"),  # 不是查询本身的语句也一并拒绝
        # 大小写不能成为后门: 关键词扫描漏掉小写写法, 白名单就是空的
        ("select * from fact_order into outfile '/tmp/x'", "OUTFILE"),
        (
            "select * from fact_order where 1=1 union select 1 into dumpfile '/tmp/y'",
            "DUMPFILE",
        ),
        ("delete from fact_order", "DELETE"),
        # 可执行注释: MySQL 会执行 `/*! … */` 里的内容, 它不是惰性注释
        ("SELECT 1 /*!80000 ; DROP TABLE t */", ";"),
        ("SELECT 1 /*!50000 INTO OUTFILE '/tmp/x' */", "OUTFILE"),
        # `--x` 不是注释 (MySQL 要求 `--` 后跟空白): 后面的 `;` 必须被看见
        ("SELECT 1 --x; DROP TABLE t", ";"),
    ],
)
def test_rejects_writes_and_multi_statements(sql: str, needle: str) -> None:
    reason = readonly_violation(sql)
    assert reason is not None, f"应当被拒绝: {sql}"
    assert needle in reason, "拒绝理由要指出撞上了哪一条, 模型才有机会照着改"


# ---------------------------------------------------------------------------
# LIMIT: 读出作者自己的界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM fact_order", ()),
        ("SELECT * FROM fact_order LIMIT 10", (10,)),
        ("select * from fact_order limit 10", (10,)),
        ("SELECT * FROM fact_order LIMIT 10, 20", (20,)),  # 逗号前是偏移, 后是行数
        ("SELECT * FROM fact_order LIMIT 10 OFFSET 5", (10,)),
        ("SELECT * FROM fact_order -- limit 10", ()),  # 注释里提一嘴不算
        ("SELECT 'limit 10' FROM t", ()),  # 字面量里的也不算
        ("SELECT * FROM (SELECT 1 LIMIT 5) t LIMIT 900", (5, 900)),  # 子查询里也有
    ],
)
def test_reads_every_own_limit(sql: str, expected: tuple[int, ...]) -> None:
    assert own_limits(sql) == expected


def test_own_limit_value_only_trusts_a_single_limit() -> None:
    """顶层与子查询各有一个 LIMIT 时不猜——猜错会把结果截得比作者要的还小."""
    assert own_limit_value("SELECT * FROM fact_order LIMIT 10") == 10
    assert own_limit_value("SELECT * FROM (SELECT 1 LIMIT 5) t LIMIT 900") is None


# ---------------------------------------------------------------------------
# LIMIT: 补齐与收紧 (文本层面, 不包裹)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected_sql", "expected_enforced"),
    [
        # 没写 → 末尾补默认档
        ("SELECT * FROM fact_order", "SELECT * FROM fact_order\nLIMIT 200", 200),
        ("SELECT * FROM fact_order;", "SELECT * FROM fact_order\nLIMIT 200", 200),
        # 写小了 → 原样放行 (作者自己界住了)
        (
            "SELECT * FROM fact_order LIMIT 50",
            "SELECT * FROM fact_order LIMIT 50",
            None,
        ),
        # 写大了 → 收到上限 (只改数字)
        (
            "SELECT * FROM fact_order LIMIT 5000",
            "SELECT * FROM fact_order LIMIT 1000",
            1000,
        ),
        (
            "SELECT * FROM fact_order LIMIT 10, 5000",
            "SELECT * FROM fact_order LIMIT 10, 1000",
            1000,
        ),
        # 多个 LIMIT 但都没超上限 → 一处都不动
        (
            "SELECT * FROM (SELECT 1 LIMIT 5) t LIMIT 900",
            "SELECT * FROM (SELECT 1 LIMIT 5) t LIMIT 900",
            None,
        ),
    ],
)
def test_enforce_limit_rewrites_only_the_number(
    sql: str, expected_sql: str, expected_enforced: int | None
) -> None:
    guarded, enforced = enforce_limit(sql)

    assert guarded == expected_sql
    assert enforced == expected_enforced


def test_limit_zero_is_not_treated_as_missing() -> None:
    """`LIMIT 0` 是「一行都不要」—— 不能被当成「没写 LIMIT」补成 200."""
    guarded, enforced = enforce_limit("SELECT * FROM fact_order LIMIT 0")

    assert guarded == "SELECT * FROM fact_order LIMIT 0"
    assert enforced is None


def test_enforce_limit_puts_the_appended_limit_on_its_own_line() -> None:
    """回归: 行尾 `-- 注释` 会把同一行的 LIMIT 一起注释掉, 等于没加."""
    guarded, _ = enforce_limit("SELECT 1 -- 只要一行")

    assert guarded.splitlines()[-1] == "LIMIT 200"


def test_enforce_limit_never_wraps_into_a_derived_table() -> None:
    """回归: 派生表包裹会让两表同名列的合法 JOIN 报 1060 Duplicate column name."""
    sql = (
        "SELECT f.date_id, d.date_id FROM fact_order f "
        "JOIN dim_date d ON f.date_id = d.date_id"
    )

    guarded, _ = enforce_limit(sql)

    assert "FROM (" not in guarded
    assert sql in guarded, "原语句要原样保留, 只在末尾补一行 LIMIT"


def test_enforce_limit_keeps_literals_intact() -> None:
    guarded, _ = enforce_limit("SELECT * FROM dim_region WHERE province = '广东省'")

    assert "'广东省'" in guarded


def test_enforce_limit_honours_custom_limits() -> None:
    tight = SqlGuardLimits(default_limit=5, cap_limit=10)

    assert enforce_limit("SELECT 1", tight) == ("SELECT 1\nLIMIT 5", 5)
    assert enforce_limit("SELECT 1 LIMIT 7", tight) == ("SELECT 1 LIMIT 7", None)
    assert enforce_limit("SELECT 1 LIMIT 700", tight) == ("SELECT 1 LIMIT 10", 10)


@pytest.mark.parametrize(
    ("default_limit", "cap_limit"),
    [(0, 100), (200, 0), (500, 100)],
)
def test_invalid_limits_are_rejected(default_limit: int, cap_limit: int) -> None:
    with pytest.raises(ValueError):
        SqlGuardLimits(default_limit=default_limit, cap_limit=cap_limit)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1 ; ", "SELECT 1"),
        ("SELECT 1", "SELECT 1"),
        ("  SELECT 1;  ", "SELECT 1"),
    ],
)
def test_strip_trailing_semicolon(sql: str, expected: str) -> None:
    assert strip_trailing_semicolon(sql) == expected


# ---------------------------------------------------------------------------
# 留痕: 报告与日志读的那一句话
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        (GuardNote(None, (), 200), "原语句无 LIMIT, 已补 LIMIT 200"),
        (GuardNote(None, (5000,), 1000), "已收紧到 1000"),
        (GuardNote(None, (50,), None), "原样放行"),
        (GuardNote(None, (5, 900), None), "原样放行"),
        (GuardNote("检测到写操作", (), None), "拒绝: 检测到写操作"),
    ],
)
def test_guard_note_describes_the_case(note: GuardNote, expected: str) -> None:
    assert expected in note.describe()


def test_guard_note_flags_what_the_guard_did() -> None:
    added = GuardNote(None, (), 200)
    assert added.had_own_limit is False
    assert added.tightened is False
    assert added.limit_enforced is True

    tightened = GuardNote(None, (5000,), 1000)
    assert tightened.had_own_limit is True
    assert tightened.tightened is True

    untouched = GuardNote(None, (50,), None)
    assert untouched.limit_enforced is False

    rejected = GuardNote("检测到写操作", (), None)
    assert rejected.limit_enforced is False


def test_default_and_cap_constants_match_the_ticket() -> None:
    """C16 开工前拍板: 默认 200 / 上限 1000 (票面建议值)."""
    assert (DEFAULT_LIMIT, LIMIT_CAP) == (200, 1000)
