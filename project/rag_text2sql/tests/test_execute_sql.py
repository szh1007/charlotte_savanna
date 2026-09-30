"""C01 回归: 执行失败不再静默.

`execute_sql` 是链路的**终点**: 它不推事件的话, 前端既收不到 `{"result": ...}`
也收不到 `{"error": ...}`, 最后一个步骤永远停在 running —— 用户看到的是转圈,
而不是失败. 事件形状对齐 `QueryService` 与 `_7_validate_sql` 的既有口径.
"""

from __future__ import annotations

from doubles import FakeDwMysqlRepository, FakeRuntime, FakeStreamWriter

from app.agent.nodes._9_execute_sql import execute_sql


async def test_success_pushes_the_result_and_no_error():
    repository = FakeDwMysqlRepository(result=[{"gmv": 1234}])
    writer = FakeStreamWriter()

    await execute_sql(
        {"sql": "select 1"}, FakeRuntime({"dw_mysql_repository": repository}, writer)
    )

    assert {"result": [{"gmv": 1234}]} in writer.events
    assert writer.errors() == []


async def test_failure_pushes_an_error_event():
    """回归本体: 失败必须推一条 error 事件."""
    repository = FakeDwMysqlRepository(error=RuntimeError("Unknown column 'gmv'"))
    writer = FakeStreamWriter()

    outcome = await execute_sql(
        {"sql": "select gmv"}, FakeRuntime({"dw_mysql_repository": repository}, writer)
    )

    assert outcome["error"] == "Unknown column 'gmv'"
    assert writer.errors() == ["Unknown column 'gmv'"], "终点节点不能只记日志不推事件"
    assert not [event for event in writer.events if "result" in event]


async def test_failure_does_not_swallow_the_error_type():
    """错误文本要能照着改 —— 原文透传, 不包装成「执行失败」四个字."""
    repository = FakeDwMysqlRepository(error=ValueError("syntax error near 'from'"))
    writer = FakeStreamWriter()

    outcome = await execute_sql(
        {"sql": "select"}, FakeRuntime({"dw_mysql_repository": repository}, writer)
    )

    assert "syntax error near 'from'" in outcome["error"]
