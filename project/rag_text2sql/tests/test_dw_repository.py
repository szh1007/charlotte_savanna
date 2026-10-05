"""dw 仓储: 护栏接在执行咽喉 + 只在连接失效时重试一次.

这一层是 C16 的落点: 图的 `_7_validate_sql` / `_9_execute_sql` 都调这两个方法,
所以「一处实现覆盖两个节点」; 跑批器也走同一份 (C15 的子类护栏已删).
"""

from __future__ import annotations

import pytest
from doubles import FakeAsyncSession
from sqlalchemy.exc import DBAPIError

from app.core.sql_guard import SqlRejectedError
from app.repositories.mysql.dw import DwMysqlRepository


def _disconnect_error() -> DBAPIError:
    """连接失效类错误 (SQLAlchemy 用它标记「这条连接废了」).

    注意 `connection_invalidated` 要**按关键字**传: 它的前一个参数是
    `hide_parameters`, 位置传参会把 True 塞错地方, 于是重试分支永远走不到.
    """
    return DBAPIError(
        "SELECT 1",
        {},
        Exception("Lost connection to MySQL server"),
        connection_invalidated=True,
    )


def _statement_error() -> DBAPIError:
    """语句类错误 (语法/未知列) —— 连接本身是好的."""
    return DBAPIError("SELECT x", {}, Exception("Unknown column 'x'"))


# ---------------------------------------------------------------------------
# 白名单
# ---------------------------------------------------------------------------


async def test_rejected_sql_never_reaches_the_database() -> None:
    session = FakeAsyncSession(result=[])
    repository = DwMysqlRepository(session)

    with pytest.raises(SqlRejectedError):
        await repository.execute_sql("DROP TABLE fact_order")

    assert session.statements == [], "被拒绝的语句绝不允许下发到数据库"
    assert repository.last_note is not None
    assert repository.last_note.rejected_reason is not None


async def test_rejection_is_recorded_for_the_report() -> None:
    session = FakeAsyncSession(result=[])
    repository = DwMysqlRepository(session)

    with pytest.raises(SqlRejectedError):
        await repository.execute_sql("DELETE FROM fact_order")

    assert [note.rejected_reason for note in repository.notes] == [
        repository.last_note.rejected_reason
    ]
    assert "DELETE" in repository.last_note.rejected_reason


# ---------------------------------------------------------------------------
# LIMIT: 下发的是包裹后的语句
# ---------------------------------------------------------------------------


async def test_select_passes_through_wrapped_with_a_note() -> None:
    session = FakeAsyncSession(result=[{"a": 1}])
    repository = DwMysqlRepository(session)

    rows = await repository.execute_sql("SELECT 1 AS a")

    assert rows == [{"a": 1}]
    assert "LIMIT 200" in session.statements[0]
    note = repository.last_note
    assert note is not None
    assert note.rejected_reason is None
    assert note.had_own_limit is False
    assert note.limit_enforced is True


async def test_oversized_limit_is_tightened() -> None:
    session = FakeAsyncSession(result=[])
    repository = DwMysqlRepository(session)

    await repository.validate_sql("SELECT 1 LIMIT 5000")

    assert "LIMIT 1000" in session.statements[0]
    assert repository.last_note.tightened is True


async def test_validate_does_not_fetch() -> None:
    session = FakeAsyncSession(result=[{"a": 1}])
    repository = DwMysqlRepository(session)

    assert await repository.validate_sql("SELECT 1") is None
    assert len(session.statements) == 1, "校验也要真执行一次 (只不取结果)"


# ---------------------------------------------------------------------------
# 重试: 只给连接失效, 且只一次
# ---------------------------------------------------------------------------


async def test_connection_loss_is_retried_once() -> None:
    session = FakeAsyncSession(script=[(_disconnect_error(), None), (None, [{"a": 1}])])
    repository = DwMysqlRepository(session)

    rows = await repository.execute_sql("SELECT 1 AS a")

    assert rows == [{"a": 1}]
    assert len(session.statements) == 2, "断连后要在新连接上再跑一次"
    assert session.rollbacks == 1, "重试前先回滚, 把失效连接清出事务"


async def test_statement_errors_are_not_retried() -> None:
    """语句错是 SQL 自己的问题 —— 重试只会再错一次, 该走校正节点."""
    session = FakeAsyncSession(error=_statement_error())
    repository = DwMysqlRepository(session)

    with pytest.raises(DBAPIError):
        await repository.execute_sql("SELECT x")

    assert len(session.statements) == 1
    assert session.rollbacks == 0


# ---------------------------------------------------------------------------
# 内部管理读: 不过护栏 (SHOW COLUMNS 被包裹会变成语法错)
# ---------------------------------------------------------------------------


async def test_internal_reads_are_not_wrapped() -> None:
    session = FakeAsyncSession(result=[])
    repository = DwMysqlRepository(session)

    await repository.get_column_types("fact_order")

    assert session.statements == ["SHOW COLUMNS from fact_order"]
    assert repository.notes == [], "内部读不留护栏痕 (它本来就不经模型)"
