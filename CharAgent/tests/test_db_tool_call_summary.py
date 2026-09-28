"""工具调用的聚合入口 (`summarize_by_tool`, issue 39): 按工具算调用情况.

场景 → 断言:
- 三个工具各若干条 → 三行, 按工具名字母序 (报告要能 diff 两次跑分, 顺序必须稳定)
- 六个状态各一条 → 每行把六个状态都数上 (挂起**单列**, 不混进成功或失败)
- 平均耗时只算有耗时的那几条 (没跑完的 `pending` 没有 `duration_ms`)
- **只数你要的那几次运行 / 那个时间窗** —— 这一条是这一片的核心防线:
  假库**不过滤 WHERE 也不排序** (`tests/doubles.py` 的 `scalars`), 于是「再筛一遍」
  那一半在真库上看着多余, 少了它假库就会把全库的行都数进来 (跑分器用的正是假库)

最后一条 (`test_the_database_and_the_fake_agree`) 走真库 (标 `pg_db`, 默认排除):
同一批行在两边的结果必须逐字相同 —— 那是上面那条防线在真库上的对照.

被测的是**聚合的口径**, 不是 SQL 长什么样 —— 后者归 `test_db_store.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from doubles import FakeRecordDatabase

from CharAgent.db import ToolCallSummary
from CharAgent.db.conversation import TurnPair
from CharAgent.db.entities import ToolCall, ToolCallStatus
from CharAgent.db.repositories.messages import MessagesRepository
from CharAgent.db.repositories.runs import RunsRepository
from CharAgent.db.repositories.threads import ThreadsRepository
from CharAgent.db.repositories.tool_calls import ToolCallsRepository, build_tool_call

# 一个固定的基准时刻: 时间窗那几条用例全靠它, 用「现在」会让断言随时钟漂
BASE = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def call(
    tool: str,
    *,
    run_id: str = "run-1",
    status: ToolCallStatus = ToolCallStatus.SUCCEEDED,
    duration_ms: int | None = 100,
    at: datetime = BASE,
    index: int = 0,
) -> ToolCall:
    """造一条工具调用行 (编号按 index 拉开, 三列主键不撞)."""
    row = build_tool_call(
        run_id=run_id,
        message_id="msg-1",
        tool_call_id=f"call_{index}",
        tool_name=tool,
        status=status,
        created_at=at,
    )
    # `build_tool_call` 造的是「刚发起」那一拍的行 (那时还谈不上耗时), 而聚合要看
    # 这一列 —— 在后头补上 (实体本来就是可变的, 与 `add_calls` 拉开时刻同一个做法)
    row.duration_ms = duration_ms
    return row


def summary_of(summaries: list[ToolCallSummary], tool: str) -> ToolCallSummary:
    """取某个工具那一行 (没有就断言失败 —— 比下标取值好读)."""
    found = [item for item in summaries if item.tool_name == tool]
    assert found, f"结果里没有 {tool!r}: {[item.tool_name for item in summaries]}"
    return found[0]


# ---------------------------------------------------------------------------
# 分组与计数
# ---------------------------------------------------------------------------


async def test_it_groups_by_tool_name_in_alphabetical_order() -> None:
    """三个工具 → 三行, 按名字排序 (diff 两次跑分时行的位置不能变)."""
    database = FakeRecordDatabase(
        tool_calls=[
            call("search_products"),
            call("add_to_cart"),
            call("search_products", index=1),
            call("get_my_order"),
        ]
    )

    summaries = await ToolCallsRepository(database).summarize_by_tool()

    assert [item.tool_name for item in summaries] == [
        "add_to_cart",
        "get_my_order",
        "search_products",
    ]
    assert summary_of(summaries, "search_products").total == 2


async def test_it_counts_every_status_separately() -> None:
    """六个状态各一条 → 六个键都在 (挂起单列, 不混进成功或失败).

    为什么挂起要单列: 它既不是成功也不是失败, 混进任何一边都会让「工具数量 A/B」
    的两组对比失真 —— 那一组里挂起多的会被算成失败多.
    """
    statuses = [
        ToolCallStatus.PENDING,
        ToolCallStatus.RUNNING,
        ToolCallStatus.SUCCEEDED,
        ToolCallStatus.FAILED,
        ToolCallStatus.CANCELLED,
        ToolCallStatus.NEEDS_APPROVAL,
    ]
    database = FakeRecordDatabase(
        tool_calls=[
            call("place_order", status=status, index=index)
            for index, status in enumerate(statuses)
        ]
    )

    summaries = await ToolCallsRepository(database).summarize_by_tool()

    row = summary_of(summaries, "place_order")
    assert row.total == 6
    for status in statuses:
        assert row.count_of(status) == 1, f"{status.value} 那一条没数上"
    assert row.count_of(ToolCallStatus.SUCCEEDED) == 1, "成功只有一个, 别的没混进来"


async def test_the_average_duration_ignores_rows_without_one() -> None:
    """平均耗时只看有耗时的行 (没跑完的 `pending` 没有 `duration_ms`)."""
    database = FakeRecordDatabase(
        tool_calls=[
            call("pay_my_order", duration_ms=100, index=0),
            call("pay_my_order", duration_ms=200, index=1),
            call("pay_my_order", duration_ms=300, index=2),
            call(
                "pay_my_order",
                status=ToolCallStatus.PENDING,
                duration_ms=None,
                index=3,
            ),
        ]
    )

    summaries = await ToolCallsRepository(database).summarize_by_tool()

    row = summary_of(summaries, "pay_my_order")
    assert row.total == 4
    assert row.avg_duration_ms == 200.0, "除以 4 会把那一条没耗时的算成 0"


async def test_a_tool_with_no_duration_at_all_has_no_average() -> None:
    """一条耗时都没有 → None, 不是 0 (「不知道」与「0 毫秒」是两回事)."""
    database = FakeRecordDatabase(
        tool_calls=[
            call("place_order", status=ToolCallStatus.PENDING, duration_ms=None)
        ]
    )

    summaries = await ToolCallsRepository(database).summarize_by_tool()

    assert summary_of(summaries, "place_order").avg_duration_ms is None


async def test_nothing_in_scope_gives_an_empty_list() -> None:
    """一条都不在范围内 → 空列表 (不是一行零)."""
    database = FakeRecordDatabase(tool_calls=[call("search_products")])

    summaries = await ToolCallsRepository(database).summarize_by_tool(
        run_ids=["run-does-not-exist"]
    )

    assert summaries == []


# ---------------------------------------------------------------------------
# 「再筛一遍」那一半 (假库不过滤 WHERE, 所以这几条在假库上才有意义)
# ---------------------------------------------------------------------------


async def test_it_only_counts_the_runs_you_ask_for() -> None:
    """给一批 run_id → 只数那几次运行的调用.

    **假库会把它全部还回来** (`scalars` 只过滤 Message 的 hidden), 所以这条在
    假库上过 = 「再筛一遍」那一半真的在跑. 真库上的对照见最后一条.
    """
    database = FakeRecordDatabase(
        tool_calls=[
            call("search_products", run_id="run-a"),
            call("search_products", run_id="run-b", index=1),
            call("add_to_cart", run_id="run-b", index=2),
        ]
    )

    summaries = await ToolCallsRepository(database).summarize_by_tool(run_ids=["run-b"])

    assert [item.tool_name for item in summaries] == ["add_to_cart", "search_products"]
    assert summary_of(summaries, "search_products").total == 1, "run-a 那条不该数进来"


async def test_it_only_counts_the_window_you_ask_for() -> None:
    """给时间窗 → 只数窗内的调用; 边界那一端**含**在内 (闭区间)."""
    database = FakeRecordDatabase(
        tool_calls=[
            call("search_products", at=BASE - timedelta(hours=1), index=0),
            call("search_products", at=BASE, index=1),
            call("search_products", at=BASE + timedelta(hours=1), index=2),
        ]
    )

    summaries = await ToolCallsRepository(database).summarize_by_tool(
        since=BASE, until=BASE + timedelta(hours=1)
    )

    assert summary_of(summaries, "search_products").total == 2, "起点那条要算进来"


async def test_the_two_conditions_are_combined() -> None:
    """run_ids 与时间窗一起给 → 两个条件都要满足 (不是任一个)."""
    database = FakeRecordDatabase(
        tool_calls=[
            call("search_products", run_id="run-a", at=BASE, index=0),
            call("search_products", run_id="run-b", at=BASE, index=1),
            call(
                "search_products",
                run_id="run-b",
                at=BASE - timedelta(hours=2),
                index=2,
            ),
        ]
    )

    summaries = await ToolCallsRepository(database).summarize_by_tool(
        run_ids=["run-b"], since=BASE
    )

    assert summary_of(summaries, "search_products").total == 1


# ---------------------------------------------------------------------------
# 真库对照 (标 pg_db, 默认排除)
# ---------------------------------------------------------------------------


@pytest.mark.pg_db
async def test_the_database_and_the_fake_agree(db) -> None:
    """同一批行: 假库与真库给出**同一份结果** (逐字相同).

    这条是上面那几条假库用例的对照 —— 少了它, 「再筛一遍」在真库上是不是多余的
    就只能靠推演; 有了它, 两边的口径被钉成一份.

    真库这边走**真实写入路径**: 先建 pending 行 (发起那一拍), 执行完再回填终态与
    耗时 (与 `test_db_recorder.py` 的用例同一条路), 不直接 INSERT 造数.
    """
    rows = [
        call("search_products", run_id="run-a", duration_ms=100, index=0),
        call("search_products", run_id="run-b", duration_ms=300, index=1),
        call(
            "add_to_cart",
            run_id="run-b",
            status=ToolCallStatus.FAILED,
            duration_ms=None,
            index=2,
        ),
    ]
    repo = ToolCallsRepository(db)
    # 真库有外键链 (会话 -> 运行 -> 消息 -> 调用; 后两条是 ticket 22 / 24 加的),
    # 前置行得先建出来 —— 假库一条都不校验, 所以这条差异只在真库上看得见
    await ThreadsRepository(db).add(thread_id="t-1", tenant_id="toy", user_id="u-1")
    for run_id in ("run-a", "run-b"):
        await RunsRepository(db).add(thread_id="t-1", run_id=run_id)
    messages = await MessagesRepository(db).add_turn(
        thread_id="t-1",
        run_id="run-a",
        turn=TurnPair(question="问一句", answer="答一句"),
    )
    # 调用挂在答复那一行上 (真实路径里是「带工具调用的那条 assistant 消息」;
    # 聚合不按 message_id 分组, 用哪一条不影响这条对照)
    message_id = messages[-1].message_id
    for row in rows:
        await repo.add(
            run_id=row.run_id,
            message_id=message_id,
            tool_call_id=row.tool_call_id,
            tool_name=row.tool_name,
            created_at=row.created_at,
        )
        if row.status != ToolCallStatus.PENDING.value:
            await repo.set_status(
                row.run_id,
                message_id,
                row.tool_call_id,
                ToolCallStatus(row.status),
                duration_ms=row.duration_ms,
            )
    fake = FakeRecordDatabase(tool_calls=rows)

    for scope in (
        {},
        {"run_ids": ["run-b"]},
        {"since": BASE + timedelta(minutes=1)},
    ):
        from_fake = await ToolCallsRepository(fake).summarize_by_tool(**scope)
        from_real = await repo.summarize_by_tool(**scope)
        assert from_fake == from_real, f"两边对不上 (scope={scope})"
