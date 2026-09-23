"""仓储的真库用例 (标 `pg_db`, 默认排除).

关注点是**端到端真的能读写**: 建表 → 写入 → 读回来 → 状态推进 → 事务回滚.
这些是纯逻辑用例测不到的 (它们只验形状与规则, 碰不到 SQL).

**为什么要一个独立的 schema**: 这是开发机的共享库 (本项目各子项目共用), 而
用例要建表、要跑迁移、还要删表. 挤在 public 里会:
- 与开发时手工建的表互相干扰
- 让 `alembic upgrade` 撞上「表已存在」(本机 public 里那张遗留的旧表就是这样)
- 用例清理时误伤别人的数据

于是每个用例在 `charagent_test` 这个 schema 里干活, 用完把表删干净 —— 开发库的
public 一个字节都不动.

真库坐标由来: 与 checkpoint 的 PG 用例共用一套配置 (根 .env 的 PGSQL_*), 拿不到
就跳过而不是失败 (本机没起 PG 时不该报一堆连接错误).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import TEST_SCHEMA
from sqlalchemy import delete, text

from CharAgent.checkpoint.postgres import PostgresCheckpointSaver
from CharAgent.checkpoint.utils.types import Checkpoint, CheckpointState
from CharAgent.db import (
    DataConfigError,
    DataStoreError,
    MessagesRepository,
    PgDatabase,
    RunsRepository,
    RunStatus,
    ThreadsRepository,
    ThreadStatus,
    ToolCallsRepository,
    ToolCallStatus,
    build_tool_call,
    conversation_turns,
    visible_transcript,
)
from CharAgent.db.schema import TABLE_NAMES, checkpoints
from CharAgent.db.schema import runs as runs_table

pytestmark = pytest.mark.pg_db


async def _make_thread(db: PgDatabase) -> str:
    """建一个会话, 返回它的编号 (多数用例都要先有一个会话)."""
    thread = await ThreadsRepository(db).add(
        tenant_id=f"test-{uuid4().hex}", user_id="u-1"
    )
    return thread.thread_id


async def _two_assistant_messages(
    messages: MessagesRepository, thread_id: str, run
) -> tuple[str, str]:
    """写两条 assistant 消息, 返回它们的编号.

    给工具调用用例当「哪条消息发起的」用 —— 上游每轮都会发一条带 tool_calls 的
    assistant 消息, 而每轮的 `tool_call_id` 都从 `call_0` 重来. 只有把「哪条
    消息发起的」一起记下来, 同名的两次调用才分得开.
    """
    rows = await messages.add_lines(
        thread_id=thread_id,
        lines=visible_transcript(
            [
                {"role": "assistant", "content": "第一轮"},
                {"role": "assistant", "content": "第二轮"},
            ]
        ),
        run_id=run.run_id,
    )
    return rows[0].message_id, rows[1].message_id


async def test_create_tables_is_idempotent(db: PgDatabase):
    """建表可以重复调用 (第二次不该报「关系已存在」).

    为什么值得单独验: 这条路径用户会走两次 —— 第一次 `ensure_schema()` 建好,
    换个进程再跑一次时还得能过. 少了幂等, 第二次使用就会炸在启动路径上.
    """
    await db.create_tables()
    await db.create_tables()

    async with db.connect() as session:
        found = (
            session.execute(
                text("select tablename from pg_tables where schemaname = :s"),
                {"s": TEST_SCHEMA},
            )
            .scalars()
            .all()
        )

    assert set(TABLE_NAMES) <= set(found)


async def test_thread_roundtrip(db: PgDatabase):
    """建会话 → 按编号取回来: 字段逐一对上 (含库端默认值)."""
    threads = ThreadsRepository(db)
    created = await threads.add(tenant_id="t-1", user_id="u-1", title="退换货")

    loaded = await threads.get(created.thread_id)

    assert loaded is not None
    assert loaded.tenant_id == "t-1"
    assert loaded.user_id == "u-1"
    assert loaded.title == "退换货"
    assert loaded.status == "active"
    assert loaded.created_at.tzinfo is not None


async def test_thread_list_is_scoped_and_ordered(db: PgDatabase):
    """会话列表: 只回本租户的, 最近活动的在前.

    「只回本租户」是 #32 多租户隔离的落点 —— 少这一条, A 公司能列出 B 公司的
    会话. 用例特地把另一个租户的数据也插进去, 再断言它**不**出现.
    """
    threads = ThreadsRepository(db)
    earlier = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    later = datetime(2026, 9, 14, 11, 0, tzinfo=UTC)
    mine = await threads.add(
        tenant_id="tenant-a", user_id="u-1", title="早的", created_at=earlier
    )
    newest = await threads.add(
        tenant_id="tenant-a", user_id="u-1", title="新的", created_at=later
    )
    await threads.add(tenant_id="tenant-b", user_id="u-9", title="别人的")

    listed = await threads.list_for_tenant("tenant-a")
    titles = [thread.title for thread in listed]

    assert set(titles) == {"早的", "新的"}
    assert titles[0] == "新的", f"最近活动的没排在前面: {titles}"
    assert {thread.thread_id for thread in listed} == {
        mine.thread_id,
        newest.thread_id,
    }


async def test_run_status_transition_is_atomic(db: PgDatabase):
    """状态推进: 合法迁移改得动, 拿过时的前置状态再来一次就改不动.

    这是并发保护的核心 —— 两个人同时点「取消」时, 只有一个能成功 (另一个的
    `from_status` 已经不成立了). 用例用「先用旧状态迁一次」来模拟那个慢了一拍的
    调用方.
    """
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    run = await runs.add(thread_id=thread_id)

    assert await runs.try_transition(run.run_id, RunStatus.CREATED, RunStatus.RUNNING)
    assert (await runs.get(run.run_id)).status == RunStatus.RUNNING

    # 再拿 created 当前置状态: 库里已经是 running, 匹配不上 -> 一行都不改
    assert not await runs.try_transition(
        run.run_id, RunStatus.CREATED, RunStatus.FAILED
    )
    assert (await runs.get(run.run_id)).status == RunStatus.RUNNING


async def test_terminal_transition_stamps_finished_at(db: PgDatabase):
    """走到终态时顺手记下结束时刻 (追问「这次跑了多久」靠它)."""
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    run = await runs.add(thread_id=thread_id, status=RunStatus.RUNNING)

    assert await runs.try_transition(run.run_id, RunStatus.RUNNING, RunStatus.FINISHED)

    loaded = await runs.get(run.run_id)
    assert loaded.finished_at is not None
    assert loaded.is_terminal is True


async def test_illegal_transition_is_rejected_before_touching_the_database(
    db: PgDatabase,
):
    """非法迁移当场报错 (不等到 SQL 那句 UPDATE 白跑一趟).

    这是「调用方逻辑错」与「并发没抢到」的分界: 前者是开发期 bug (让终态回到
    运行中), 必须响; 后者是正常现象, 返回 False 即可.
    """
    from CharAgent.db.errors import InvalidTransitionError

    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    run = await runs.add(thread_id=thread_id, status=RunStatus.FINISHED)

    with pytest.raises(InvalidTransitionError):
        await runs.try_transition(run.run_id, RunStatus.FINISHED, RunStatus.RUNNING)


async def test_duplicate_request_id_is_rejected_with_a_readable_error(db: PgDatabase):
    """同一个 request_id 建两次: 报错且**报错信息看得懂**.

    幂等键的唯一约束是「重复提交不会建出第二条运行」的最后一道闸 (应用层先查
    后插有并发窗口). 裸的 `UniqueViolation` 对调用方没用 —— 它不知道是哪一条、
    为什么重复, 所以仓储把这句包装过.
    """
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    request_id = f"req-{uuid4().hex}"
    await runs.add(thread_id=thread_id, request_id=request_id)

    with pytest.raises(DataStoreError) as excinfo:
        await runs.add(thread_id=thread_id, request_id=request_id)

    assert request_id in str(excinfo.value)


async def test_failed_transaction_leaves_nothing_behind(db: PgDatabase):
    """事务中途失败: 那次事务里写的东西一条都不留.

    为什么这条重要: 「建运行 + 写用户消息」这类操作必须同生共死. 少了事务, 中间
    挂掉会留下「运行建好了但消息没写进去」的半截数据 —— 用户看到一段没有问题的
    回答, 谁也说不清发生了什么.
    """
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    orphan_id = f"rollback-{uuid4().hex}"

    with pytest.raises(DataStoreError):
        async with db.connect() as session:
            session.execute(
                text(
                    "insert into charagent_runs (run_id, thread_id, status) "
                    "values (:run_id, :thread_id, 'created')"
                ),
                {"run_id": orphan_id, "thread_id": thread_id},
            )
            # 故意在同一事务里制造一个失败 (除零)
            session.execute(text("select 1/0"))

    assert await runs.get(orphan_id) is None


async def test_conversation_and_transcript_reads_differ(db: PgDatabase):
    """两种读法各取所需: 会话历史只给一问一答, 全量记录连内部件一起给.

    这是消息分层验收在**库这一层**的样子: 同样一段数据, 接口拿到的与
    排查拿到的不是同一份.
    """
    messages = MessagesRepository(db)
    thread_id = await _make_thread(db)
    wire = [
        {"role": "user", "content": "订单到哪了"},
        {
            "role": "assistant",
            "content": "让我先查一下",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "q", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "content": "已发货"},
        {"role": "assistant", "content": "已经发货了"},
    ]
    await messages.add_lines(
        thread_id=thread_id, lines=visible_transcript(wire), run_id=None
    )

    conversation = await messages.list_conversation(thread_id)
    transcript = await messages.list_transcript(thread_id)

    assert [row.role for row in conversation] == ["user", "assistant"]
    assert [row.role for row in transcript] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert [row.hidden for row in conversation] == [False, False]


async def test_add_turn_writes_a_visible_question_answer_pair(db: PgDatabase):
    """`add_turn` 写两行且都可见, 顺序稳定 (问在前、答在后).

    顺序稳定性靠的是两条消息的 `created_at` 差 1 微秒 —— 同一时刻的话就只能靠
    主键排, 顺序就成随机的了.
    """
    messages = MessagesRepository(db)
    thread_id = await _make_thread(db)
    turn = conversation_turns(
        [{"role": "user", "content": "问"}, {"role": "assistant", "content": "答"}],
        "答",
    )[0]

    rows = await messages.add_turn(thread_id=thread_id, turn=turn)

    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[0].created_at < rows[1].created_at

    pairs = await messages.conversation_pairs(thread_id)
    assert len(pairs) == 1
    assert pairs[0].question == "问"
    assert pairs[0].answer == "答"


async def test_reasoning_is_stored_separately_from_content(db: PgDatabase):
    """思维链独立成列存储 (与正文分开, 前端折叠展示用).

    注意这与「要不要给用户看」无关: 这一列的 payload 是给 Thinking 区折叠展示的,
    而它所在那条消息**是不是**可见由 `hidden` 决定 —— 两码事.
    """
    messages = MessagesRepository(db)
    thread_id = await _make_thread(db)
    turn = conversation_turns(
        [
            {"role": "user", "content": "问"},
            {"role": "assistant", "content": "答", "reasoning_content": "想了想"},
        ],
        "答",
    )[0]

    await messages.add_turn(thread_id=thread_id, turn=turn)
    rows = await messages.list_conversation(thread_id)

    assistant_row = rows[-1]
    assert assistant_row.content == "答"
    assert assistant_row.reasoning == "想了想"


async def test_same_tool_call_id_across_two_turns_does_not_collide(db: PgDatabase):
    """同一个 run 里两轮都叫 `call_0`: **不撞键** (复合主键的意义).

    真实上游每轮都从 `call_0` 重新编号 —— 这条不是假设, 是线上真实形态 (在
    `checkpoint/utils/pending.py` 里有同样的记载). 单列主键在这里就会炸.
    """
    calls = ToolCallsRepository(db)
    runs = RunsRepository(db)
    messages = MessagesRepository(db)
    thread_id = await _make_thread(db)
    run = await runs.add(thread_id=thread_id)

    # 两轮各发一条 call_0 —— 靠 message_id 区分 (上游每轮重新编号, 这是真实形态)
    first_turn, second_turn = await _two_assistant_messages(messages, thread_id, run)
    await calls.add(
        run_id=run.run_id,
        message_id=first_turn,
        tool_call_id="call_0",
        tool_name="query_order",
    )
    await calls.add(
        run_id=run.run_id,
        message_id=second_turn,
        tool_call_id="call_0",
        tool_name="query_logistics",
    )

    listed = await calls.list_for_run(run.run_id)
    assert [call.tool_name for call in listed] == ["query_order", "query_logistics"]


async def test_add_calls_success_path_keeps_the_given_order(db: PgDatabase):
    """一批工具调用按**传入顺序**落库, 且时刻依次递增 1 微秒.

    这条盯的是 `add_calls` 里那段「以第一条的时刻为基准逐条 +1μs」—— 少了它,
    同一轮的多条调用 `created_at` 完全相同, `list_for_run` 的顺序就只能靠主键
    (而主键里有模型给的编号), 顺序变成随机的. 那是**静默**的错误: 接口不报错,
    只是每次查出来的顺序可能不一样.
    """
    import CharAgent.db as db_module

    calls = ToolCallsRepository(db)
    runs = RunsRepository(db)
    messages = MessagesRepository(db)
    thread_id = await _make_thread(db)
    run = await runs.add(thread_id=thread_id)
    turn, _ = await _two_assistant_messages(messages, thread_id, run)

    rows = [
        db_module.build_tool_call(
            run_id=run.run_id,
            message_id=turn,
            tool_call_id=f"call_{i}",
            tool_name=name,
        )
        for i, name in enumerate(["query_order", "query_logistics", "send_sms"])
    ]
    base = rows[0].created_at

    await calls.add_calls(run_id=run.run_id, calls=rows)

    listed = await calls.list_for_run(run.run_id)
    assert [call.tool_name for call in listed] == [
        "query_order",
        "query_logistics",
        "send_sms",
    ]
    # 落库的时刻按传入顺序逐条 +1 微秒 (存的是同一个 run 的同一轮, 必须能排出序)
    moments = [call.created_at for call in listed]
    assert moments == sorted(moments), "落库后顺序与传入顺序不一致"
    assert moments[1] - base >= timedelta(microseconds=1)
    assert moments[2] - base >= timedelta(microseconds=2)
    # 顺带钉住: 这个方法会**原地改写**传入的那些对象 (调用方若复用它们要知情)
    assert rows[0].created_at == base


async def test_add_calls_with_empty_list_is_a_noop(db: PgDatabase):
    """空列表什么都不做 (不报错、不写库) —— 上游「这一轮没调工具」是正常情况."""
    calls = ToolCallsRepository(db)
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    run = await runs.add(thread_id=thread_id)

    await calls.add_calls(run_id=run.run_id, calls=[])

    assert await calls.list_for_run(run.run_id) == []


async def test_conversation_pairs_handles_unanswered_questions(db: PgDatabase):
    """一问一答的配对: 连续两问时前一条如实记成「没答出来」, 不丢掉.

    真实场景: 用户连发两条, agent 只答了后一条 (或人工只回了后一条). 若配对逻辑
    见第二问就覆盖第一问, 那条问题会**凭空消失** —— 用户会以为自己的话没发出去.
    """
    from CharAgent.db.entities import Message, MessageRole

    messages = MessagesRepository(db)
    thread_id = await _make_thread(db)
    base = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)

    def _row(role: str, content: str, offset: int) -> Message:
        return Message(
            message_id=f"m{offset}",
            thread_id=thread_id,
            run_id=None,
            role=role,
            content=content,
            reasoning=None,
            tool_call_ids=[],
            hidden=False,
            created_at=base + timedelta(seconds=offset),
        )

    await messages.add_messages(
        [
            _row(MessageRole.USER.value, "第一问", 0),
            _row(MessageRole.USER.value, "第二问", 1),
            _row(MessageRole.ASSISTANT.value, "答第二问", 2),
            _row(MessageRole.USER.value, "第三问还没答", 3),
        ]
    )

    pairs = await messages.conversation_pairs(thread_id)

    assert [(pair.question, pair.answer) for pair in pairs] == [
        ("第一问", None),  # 没等到答案就来下一问 -> 如实记成没答
        ("第二问", "答第二问"),
        ("第三问还没答", None),  # 末尾悬挂的问题也不丢
    ]


async def test_conversation_reads_honour_limit(db: PgDatabase):
    """`limit` 取的是**最近** N 条, 但返回顺序仍是早 -> 晚.

    分页时最容易错的就是这个: 库里倒着取最快, 忘了翻正的话前端会把对话读成倒序.
    """
    from CharAgent.db.entities import Message, MessageRole

    messages = MessagesRepository(db)
    thread_id = await _make_thread(db)
    base = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    rows = [
        Message(
            message_id=f"m{i}",
            thread_id=thread_id,
            run_id=None,
            role=MessageRole.USER.value,
            content=f"第{i}条",
            reasoning=None,
            tool_call_ids=[],
            hidden=False,
            created_at=base + timedelta(seconds=i),
        )
        for i in range(5)
    ]
    await messages.add_messages(rows)

    recent = await messages.list_conversation(thread_id, limit=2)

    assert [row.content for row in recent] == ["第3条", "第4条"]  # 最近两条, 正序
    assert await messages.list_conversation(thread_id, limit=0) == []


async def test_tool_call_approval_records_who_and_when(db: PgDatabase):
    """审批动作记下是谁、什么时候批的 (#25 HITL).

    这两列是审计要求: 高危动作 (退款) 批了之后要能回答「谁批的、什么时候」.
    """
    calls = ToolCallsRepository(db)
    runs = RunsRepository(db)
    messages = MessagesRepository(db)
    thread_id = await _make_thread(db)
    run = await runs.add(thread_id=thread_id)
    turn, _ = await _two_assistant_messages(messages, thread_id, run)
    await calls.add(
        run_id=run.run_id,
        message_id=turn,
        tool_call_id="call_0",
        tool_name="refund",
        arguments='{"amount": "128.00"}',
        status=ToolCallStatus.NEEDS_APPROVAL,
    )

    assert await calls.set_status(
        run.run_id, turn, "call_0", ToolCallStatus.SUCCEEDED, approved_by="ops-1"
    )

    loaded = await calls.get(run.run_id, turn, "call_0")
    assert loaded.status == "succeeded"
    assert loaded.approved_by == "ops-1"
    assert loaded.approved_at is not None


async def test_batch_insert_cannot_mix_threads(db: PgDatabase):
    """一批消息只能属于一个会话 (混着写几乎总是调用方把两个会话的数据串了).

    这类错如果不拦, 现象会是「A 用户的对话里出现 B 用户的消息」—— 数据串了就
    再也分不回来, 所以在写入前就报出来.
    """
    from CharAgent.db.entities import Message
    from CharAgent.db.errors import DataConfigError

    messages = MessagesRepository(db)
    first = await _make_thread(db)
    second = await _make_thread(db)

    rows = [
        Message(
            message_id=uuid4().hex,
            thread_id=first,
            run_id=None,
            role="user",
            content="问",
            reasoning=None,
            tool_call_ids=[],
            hidden=False,
            created_at=datetime.now(UTC),
        ),
        Message(
            message_id=uuid4().hex,
            thread_id=second,
            run_id=None,
            role="user",
            content="另一段会话的问",
            reasoning=None,
            tool_call_ids=[],
            hidden=False,
            created_at=datetime.now(UTC),
        ),
    ]

    with pytest.raises(DataConfigError):
        await messages.add_messages(rows)


async def test_batch_tool_calls_cannot_mix_runs(db: PgDatabase):
    """一批工具调用只能属于一次运行 (理由同上, 主键的一半就是 run_id)."""
    from CharAgent.db.errors import DataConfigError

    calls = ToolCallsRepository(db)
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    first = await runs.add(thread_id=thread_id)
    second = await runs.add(thread_id=thread_id)

    messages = MessagesRepository(db)
    first_turn, _ = await _two_assistant_messages(messages, thread_id, first)
    second_turn, _ = await _two_assistant_messages(messages, thread_id, second)
    rows = [
        build_tool_call(
            run_id=first.run_id,
            message_id=first_turn,
            tool_call_id="call_0",
            tool_name="a",
        ),
        build_tool_call(
            run_id=second.run_id,
            message_id=second_turn,
            tool_call_id="call_0",
            tool_name="b",
        ),
    ]

    with pytest.raises(DataConfigError):
        await calls.add_calls(run_id=first.run_id, calls=rows)


async def test_injected_engine_survives_dispose(db: PgDatabase):
    """`dispose()` **不关**注入进来的引擎 (谁建的谁负责).

    反例的后果很隐蔽: 测试 fixture 常注入一个共享引擎, 某个用例调 dispose 把池子
    关掉之后, 后面**所有**用例都会报「连接已关闭」—— 而报错的地方看着完全无关.
    """
    await db.dispose()

    async with db.connect() as session:
        assert session.execute(text("select 1")).scalar() == 1


async def test_thread_list_can_filter_by_owner(db: PgDatabase):
    """`list_for_tenant(user_id=...)` 只回该用户的会话 —— 用户维度必须也隔离.

    租户维度测过、用户维度没测: 漏了就是「同一租户下能看到别人的会话」—— 多租户
    只挡住了「跨公司」, 挡不住「同公司里跨人」.
    """
    threads = ThreadsRepository(db)
    mine = await threads.add(tenant_id="tenant-a", user_id="alice", title="我的")
    await threads.add(tenant_id="tenant-a", user_id="bob", title="别人的")
    await threads.add(tenant_id="tenant-b", user_id="alice", title="别租户的")

    only_mine = await threads.list_for_tenant("tenant-a", user_id="alice")

    assert [thread.thread_id for thread in only_mine] == [mine.thread_id]
    # limit <= 0 的早返回 (调用方算出个 0 时该拿空列表, 不是拿全部)
    assert await threads.list_for_tenant("tenant-a", limit=0) == []


async def test_thread_duplicate_id_is_rejected_with_a_readable_error(db: PgDatabase):
    """同一个 thread_id 建两次: 报错且**报错信息看得懂** (与 runs 同一套做法)."""
    from CharAgent.db.errors import DataStoreError

    threads = ThreadsRepository(db)
    created = await threads.add(tenant_id="t-1", user_id="u-1")

    with pytest.raises(DataStoreError) as excinfo:
        await threads.add(tenant_id="t-1", user_id="u-1", thread_id=created.thread_id)

    assert created.thread_id in str(excinfo.value)


async def test_get_run_by_request_id(db: PgDatabase):
    """幂等键是 `POST /runs` 的入口: 命中返回那条运行, 没命中返回 None.

    这条是「同一个 request_id 重复提交直接返回已有结果」的查询侧 —— 上层接线时
    就靠它.
    """
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    request_id = f"req-{uuid4().hex}"
    created = await runs.add(thread_id=thread_id, request_id=request_id)

    found = await runs.get_by_request_id(request_id)

    assert found is not None
    assert found.run_id == created.run_id
    assert await runs.get_by_request_id("从没见过的键") is None


async def test_set_status_bypasses_the_state_machine_on_purpose(db: PgDatabase):
    """`set_status()` **故意不做校验** —— 它是修数据/对账的口子, 不是业务路径.

    为什么值得单独钉住: 它与 `try_transition` 同在 `RunsRepository` 上, 名字也像,
    很容易被当成「另一个改状态的方法」拿去用. 这条用例把两者的分工写死在断言里:
    非法迁移 `try_transition` 会抛 `InvalidTransitionError`, 而 `set_status` 照改.
    """
    from CharAgent.db.errors import InvalidTransitionError

    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    run = await runs.add(thread_id=thread_id, status=RunStatus.FINISHED)

    # 业务路径: 终态走不出去, 当场报错
    with pytest.raises(InvalidTransitionError):
        await runs.try_transition(run.run_id, RunStatus.FINISHED, RunStatus.RUNNING)

    # 修数据路径: 明知非法也改得动, 并顺手记下失败原因
    assert await runs.set_status(
        run.run_id, RunStatus.FAILED, error={"code": "repair", "message": "对账修正"}
    )

    loaded = await runs.get(run.run_id)
    assert loaded.status == RunStatus.FAILED.value
    assert loaded.error == {"code": "repair", "message": "对账修正"}
    assert loaded.finished_at is not None  # 终态会补结束时刻
    assert await runs.set_status("没有这个运行", RunStatus.FAILED) is False


async def test_empty_batches_are_noops(db: PgDatabase):
    """空批次什么都不做 (不报错、不写库) —— 「这次没有」是正常情况, 不是错误."""
    messages = MessagesRepository(db)
    thread_id = await _make_thread(db)

    await messages.add_messages([])

    assert await messages.list_conversation(thread_id) == []


async def test_engine_configuration_is_lazy_and_reusable(db: PgDatabase):
    """同一个 database 反复开事务没问题 (连接从池子里借还).

    早期是「一条连接 + 一把锁排队」, 换到 SQLAlchemy 之后由它自带的连接
    池负责 —— 这条用例确认反复开关事务不会把连接用坏.
    """
    for _ in range(5):
        async with db.connect() as session:
            assert session.execute(text("select 1")).scalar() == 1


# ---------------------------------------------------------------------------
# 记录那条线的读写口 (ticket 17): 会话列表 / 活动时刻 / 已跑完的运行行
# ---------------------------------------------------------------------------


async def _thread_with_a_visible_message(
    db: PgDatabase,
    *,
    tenant_id: str,
    user_id: str,
    moment: datetime | None = None,
    status: ThreadStatus = ThreadStatus.ACTIVE,
):
    """建一个「聊过一句」的会话 (会话列表要的正是这种)."""
    threads = ThreadsRepository(db)
    thread = await threads.add(
        tenant_id=tenant_id,
        user_id=user_id,
        title="聊过",
        status=status,
        created_at=moment,
    )
    await MessagesRepository(db).add_lines(
        thread_id=thread.thread_id,
        lines=visible_transcript([{"role": "user", "content": "订单到哪了"}]),
    )
    return thread


async def _thread_titled(
    db: PgDatabase, *, tenant_id: str, user_id: str, title: str, body: str
):
    """建一个标题与正文都由调用方给的会话 (搜索用例要拿这两处当靶子)."""
    thread = await ThreadsRepository(db).add(
        tenant_id=tenant_id, user_id=user_id, title=title
    )
    await MessagesRepository(db).add_lines(
        thread_id=thread.thread_id,
        lines=visible_transcript([{"role": "user", "content": body}]),
    )
    return thread


async def test_list_active_skips_shells_internals_and_closed_threads(db: PgDatabase):
    """会话列表只要「还在聊且聊过话」的: 空壳 / 只剩内部件 / 已归档都不进.

    前端左侧那一栏点进去必须有点东西 —— 空壳会话 (刚点了「新建」) 与只有工具
    回填的会话点开都是空白, 列在那里只是噪音.
    """
    threads = ThreadsRepository(db)
    messages = MessagesRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    kept = await _thread_with_a_visible_message(db, tenant_id=tenant, user_id="u-1")
    await threads.add(tenant_id=tenant, user_id="u-1", title="空壳")
    internals_only = await threads.add(
        tenant_id=tenant, user_id="u-1", title="只有内部件"
    )
    await messages.add_lines(
        thread_id=internals_only.thread_id,
        lines=visible_transcript(
            [
                {"role": "tool", "content": "工具回填"},
                {
                    "role": "assistant",
                    "content": "过程中",
                    "tool_calls": [
                        {"id": "call_0", "type": "function", "function": {"name": "q"}}
                    ],
                },
            ]
        ),
    )
    closed = await _thread_with_a_visible_message(
        db, tenant_id=tenant, user_id="u-1", status=ThreadStatus.CLOSED
    )

    listed = await threads.list_active_with_messages(tenant)

    assert [thread.thread_id for thread in listed] == [kept.thread_id]
    assert closed.thread_id not in {thread.thread_id for thread in listed}


async def test_list_active_is_scoped_to_tenant_and_owner(db: PgDatabase):
    """会话列表按 (租户, 属主) 过滤 —— 换一个租户或换一个人都看不别人的.

    这是 #32 那条多租户隔离在**记录**这条线上的落点: 用例把别人的数据也插进去,
    再断言它不出现 (少这一条, 前端列表就漏了别人的会话).
    """
    threads = ThreadsRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    mine = await _thread_with_a_visible_message(db, tenant_id=tenant, user_id="u-1")
    await _thread_with_a_visible_message(db, tenant_id=tenant, user_id="u-2")
    await _thread_with_a_visible_message(
        db, tenant_id=f"tenant-{uuid4().hex}", user_id="u-1"
    )

    only_mine = await threads.list_active_with_messages(tenant, user_id="u-1")
    whole_tenant = await threads.list_active_with_messages(tenant)

    assert [thread.thread_id for thread in only_mine] == [mine.thread_id]
    assert len(whole_tenant) == 2, "不给 user_id 就是这个租户下所有人的"


async def test_list_active_is_ordered_by_last_activity(db: PgDatabase):
    """按最后活动时刻倒序 —— 刚聊过的排最前 (会话列表的顺序契约)."""
    threads = ThreadsRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    earlier = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    later = datetime(2026, 9, 20, 11, 0, tzinfo=UTC)
    older = await _thread_with_a_visible_message(
        db, tenant_id=tenant, user_id="u-1", moment=earlier
    )
    newer = await _thread_with_a_visible_message(
        db, tenant_id=tenant, user_id="u-1", moment=later
    )

    assert [
        thread.thread_id for thread in await threads.list_active_with_messages(tenant)
    ] == [newer.thread_id, older.thread_id]

    # 老的那个又聊了一句 -> 它跳到最前面 (这就是「每轮刷 updated_at」的意义)
    assert await threads.touch(older.thread_id, moment=later + timedelta(hours=1))

    assert [
        thread.thread_id for thread in await threads.list_active_with_messages(tenant)
    ] == [older.thread_id, newer.thread_id]


async def test_the_saver_creates_the_tables_it_depends_on(db: PgDatabase) -> None:
    """saver 的「自己建表」要连**依赖**一起建 (ticket 22).

    帧表的 `run_id` 外键指着 `charagent_runs`, 后者又指着 `charagent_threads` ——
    只建帧表的话 Postgres 当场拒绝建表 (被引用的表不存在). 正式环境的表结构归
    alembic 管, 这条路是「没跑迁移也想自己起来」的那条 (框架 CLI 演示、单测);
    少了它, 现象是**第一次存帧**才炸, 而且报的是「表不存在」这种离原因很远的错.

    先把三张表删干净再让 saver 建 —— 不删的话 `create_all` 见到表已存在就跳过,
    这条用例反而什么都验不到.
    """
    async with db.connect() as session:
        for table in (
            "charagent_checkpoints",
            "charagent_runs",
            "charagent_threads",
        ):
            session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))

    await PostgresCheckpointSaver(engine=db.engine()).ensure_schema()

    async with db.connect() as session:
        present = session.execute(
            text(
                "SELECT to_regclass('charagent_checkpoints') IS NOT NULL AS frames,"
                " to_regclass('charagent_runs') IS NOT NULL AS runs,"
                " to_regclass('charagent_threads') IS NOT NULL AS threads"
            )
        ).one()
    assert all(present), f"三张表 (帧 / 运行 / 会话) 都该被建出来: {present}"


async def test_frames_and_runs_point_at_each_other_and_unlink_on_delete(
    db: PgDatabase,
):
    """ticket 22 的三列在**真库**上对得上, 两条外键都是 `ON DELETE SET NULL`.

    假库那条路 (`test_frame_run_linkage.py`) 断的是「装配把编号传对了」; 这里断的是
    **库这一层**: 外键真的建出来了 (只在 no-migration 的环境里才需要连依赖一起建,
    见 saver 的 `_create_table`), 而且删掉一头时另一头**置空而不是跟着消失** ——
    帧比账目行活得久 (快照是断点续跑的依据), 运行行比帧活得久 (账目是审计用的),
    互相删对方都不该毁掉。
    """
    threads = ThreadsRepository(db)
    runs = RunsRepository(db)
    thread = await threads.add(tenant_id="toy-linkage", user_id="u-1", title="联表")
    run = await runs.add(thread_id=thread.thread_id, status=RunStatus.RUNNING)
    saver = PostgresCheckpointSaver(engine=db.engine())
    frame = Checkpoint.create(
        thread_id=thread.thread_id,
        loop_id="loop-linkage",
        run_id=run.run_id,
        turn_number=1,
        state=CheckpointState(messages=[{"role": "user", "content": "在吗"}]),
    )
    await saver.save(frame)
    assert await runs.finish(
        run.run_id, status=RunStatus.FINISHED, last_checkpoint_id=frame.checkpoint_id
    )

    # 帧 → 运行行, 运行行 → 帧
    loaded_frame = await saver.load(frame.checkpoint_id)
    loaded_run = await runs.get(run.run_id)
    assert loaded_frame.run_id == run.run_id
    assert loaded_run.last_checkpoint_id == frame.checkpoint_id

    # 情形一 —— 删掉**帧**: 运行行还在, 只是那一列置空 (SET NULL, 不跟着删行)
    async with db.connect() as session:
        session.execute(
            delete(checkpoints).where(
                checkpoints.c.checkpoint_id == frame.checkpoint_id
            )
        )
    assert (await runs.get(run.run_id)).last_checkpoint_id is None

    # 情形二 —— 删掉**运行行**: 帧还在, 它那一列同样置空 (各用一组自己的行)
    frame2 = Checkpoint.create(
        thread_id=thread.thread_id,
        loop_id="loop-linkage-2",
        run_id=run.run_id,
        turn_number=2,
        state=CheckpointState(messages=[{"role": "user", "content": "还在吗"}]),
    )
    await saver.save(frame2)
    async with db.connect() as session:
        session.execute(delete(runs_table).where(runs_table.c.run_id == run.run_id))
    assert (await saver.load(frame2.checkpoint_id)).run_id is None


async def test_touch_reports_whether_it_hit_a_row(db: PgDatabase):
    """`touch` 如实回报「改到了没有」—— 没这个会话时是 False."""
    threads = ThreadsRepository(db)

    assert await threads.touch("没有这个会话", moment=datetime.now(UTC)) is False


async def test_finish_settles_the_row_that_begin_created(db: PgDatabase):
    """已跑完的运行**推进那一行**到终态 (行是 `add` 先建出来的, ticket 22).

    为什么不再是一条 INSERT 落成终态: 快照帧在运行**中途**逐轮落盘, 而
    `checkpoints.run_id` 是指向这一行的外键 —— 行必须在跑之前就在 (见 `begin` /
    `add`), 于是收尾必然是一次带 WHERE 的 UPDATE. 终态与 `finished_at` 一起写,
    库里因此不会出现「状态是终态而 `finished_at` 为空」的行 (NULL 的语义是「还没跑
    到终点」).
    """
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    moment = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    run = await runs.add(thread_id=thread_id, status=RunStatus.RUNNING)
    assert await runs.finish(
        run.run_id,
        status=RunStatus.FINISHED,
        turn_count=3,
        total_tokens=128,
        moment=moment,
    )

    loaded = await runs.get(run.run_id)
    assert loaded.status == RunStatus.FINISHED
    assert loaded.is_terminal is True
    assert loaded.finished_at == moment
    assert (loaded.turn_count, loaded.total_tokens) == (3, 128)
    # 调用方没给模型名 / 提示词版本 / 用量分解: 那几列如实留空 (NULL = 没有, 不是
    # 「零」); 花费那一列留给 L3 的成本记账
    assert (loaded.model, loaded.prompt_version, loaded.error) == (None, None, None)
    assert (loaded.input_tokens, loaded.cache_hit_tokens) == (None, None)


async def test_finish_writes_the_usage_breakdown(db: PgDatabase) -> None:
    """真库往返: 用量分解五列与提示词版本都写得进去、读得回来.

    真正要验的是**列本身** (0002 那条迁移建出来的类型与可空性): 单元测试用的是假
    库, 列不存在它照样绿.
    """
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)

    run = await runs.add(thread_id=thread_id, status=RunStatus.RUNNING)
    await runs.finish(
        run.run_id,
        status=RunStatus.FINISHED,
        total_tokens=4441,
        input_tokens=4120,
        output_tokens=321,
        reasoning_tokens=180,
        cache_hit_tokens=2048,
        cache_miss_tokens=2072,
        prompt_version="system/v2",
    )

    loaded = await runs.get(run.run_id)
    assert (loaded.input_tokens, loaded.output_tokens) == (4120, 321)
    assert loaded.reasoning_tokens == 180
    assert (loaded.cache_hit_tokens, loaded.cache_miss_tokens) == (2048, 2072)
    assert loaded.prompt_version == "system/v2"
    assert loaded.input_tokens + loaded.output_tokens == loaded.total_tokens


async def test_finish_refuses_a_status_that_is_not_an_ending(db: PgDatabase):
    """非要给个非终态就当场报错 (那是一次还没结束的运行, 该走状态推进那几条)."""
    runs = RunsRepository(db)
    thread_id = await _make_thread(db)
    run = await runs.add(thread_id=thread_id, status=RunStatus.RUNNING)

    with pytest.raises(DataConfigError):
        await runs.finish(run.run_id, status=RunStatus.RUNNING)


# ---------------------------------------------------------------------------
# 会话管理: 置顶 / 改名 / 删除 / 搜索 (ticket 20)
# ---------------------------------------------------------------------------


async def test_pinned_threads_come_first(db: PgDatabase):
    """置顶的排最前, 且**最近置顶的**更靠前 —— 这正是不用布尔而用时刻的理由.

    没置顶的那些相互之间仍按最后活动倒序: 置顶是插在整张表前面的一小撮, 不把后面
    的顺序打乱.
    """
    threads = ThreadsRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    day = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    newest = await _thread_with_a_visible_message(
        db, tenant_id=tenant, user_id="u-1", moment=day + timedelta(hours=2)
    )
    middle = await _thread_with_a_visible_message(
        db, tenant_id=tenant, user_id="u-1", moment=day + timedelta(hours=1)
    )
    oldest = await _thread_with_a_visible_message(
        db, tenant_id=tenant, user_id="u-1", moment=day
    )

    async def listed() -> list[str]:
        return [t.thread_id for t in await threads.list_active_with_messages(tenant)]

    async def pin(thread_id: str, pinned: bool, *, moment=None) -> None:
        assert await threads.set_pinned(
            thread_id, pinned, tenant_id=tenant, user_id="u-1", moment=moment
        )

    # 没置顶时: 纯按最后活动倒序
    assert await listed() == [newest.thread_id, middle.thread_id, oldest.thread_id]

    # 置顶最老的那个: 它跳到最前, 其余两个相互顺序不变
    await pin(oldest.thread_id, True, moment=day)
    assert await listed() == [oldest.thread_id, newest.thread_id, middle.thread_id]

    # 再置顶中间那个: **最近置顶的**排在更前面 (布尔表达不了这件事)
    await pin(middle.thread_id, True, moment=day + timedelta(hours=1))
    assert await listed() == [middle.thread_id, oldest.thread_id, newest.thread_id]

    # 全部取消置顶: 回到按活动倒序
    await pin(middle.thread_id, False)
    await pin(oldest.thread_id, False)
    assert await listed() == [newest.thread_id, middle.thread_id, oldest.thread_id]


async def test_soft_delete_hides_the_thread_but_keeps_every_row(db: PgDatabase):
    """删除是**软删**: 两个列表里都没了, 而行与消息一条不少, 重删也不报错.

    行与消息留着不是偷懒 —— 成本记账 (L3) 挂在 `charagent_runs` 上, 硬删会让
    「上周花了多少钱」凭空少一块; 而用户要的「删除」本来就是「从列表里消失」.

    这条同时钉住 ticket 20 点名要想清楚的那句「**删除后列表不再返回它, 但历史仍
    读得到**」: 前一半是上面两条列表断言, 后一半是「消息一行不少」+「按编号仍取
    得到那一行」—— 而 `/history` 那条路读的正是这两样, 它自己**没有**任何
    `deleted_at` 过滤 (那条路由只认会话编号), 所以服务端数据还在 = 历史读得到.
    """
    threads = ThreadsRepository(db)
    messages = MessagesRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    doomed = await _thread_with_a_visible_message(db, tenant_id=tenant, user_id="u-1")
    kept = await _thread_with_a_visible_message(db, tenant_id=tenant, user_id="u-1")
    body_before = await messages.list_conversation(doomed.thread_id)

    first = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
    assert await threads.soft_delete(
        doomed.thread_id, tenant_id=tenant, user_id="u-1", moment=first
    )

    # 两个列表都不再给它 (用户的「删掉」= 从我的列表里消失)
    assert [t.thread_id for t in await threads.list_active_with_messages(tenant)] == [
        kept.thread_id
    ]
    assert doomed.thread_id not in {
        t.thread_id for t in await threads.list_for_tenant(tenant)
    }
    # 数据一条没少
    still_there = await threads.get(doomed.thread_id)
    assert still_there is not None
    assert still_there.deleted_at == first
    assert [m.content for m in await messages.list_conversation(doomed.thread_id)] == [
        m.content for m in body_before
    ]

    # 再删一次: 照样命中一行 (不报错, 上层回 200 而不是 404), 列表也还是没有它
    later = first + timedelta(hours=3)
    assert await threads.soft_delete(
        doomed.thread_id, tenant_id=tenant, user_id="u-1", moment=later
    )
    assert [t.thread_id for t in await threads.list_active_with_messages(tenant)] == [
        kept.thread_id
    ]


async def test_renaming_does_not_touch_last_activity(db: PgDatabase):
    """改标题**不动 `updated_at`** —— 否则改个名字就把会话顶到列表最前.

    「最后活动时刻」说的是聊过话, 而改名不是活动. 这条容易在"顺手刷新一下"里丢掉
    (`touch` 与 `set_title` 都写这一列, 只有用户改名这一条不写).
    """
    threads = ThreadsRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    day = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    newer = await _thread_with_a_visible_message(
        db, tenant_id=tenant, user_id="u-1", moment=day + timedelta(hours=1)
    )
    older = await _thread_with_a_visible_message(
        db, tenant_id=tenant, user_id="u-1", moment=day
    )

    assert await threads.update_title(
        older.thread_id, "退换货政策", tenant_id=tenant, user_id="u-1"
    )

    renamed = await threads.get(older.thread_id)
    assert renamed is not None
    assert renamed.title == "退换货政策"
    assert renamed.updated_at == day, "改标题不算「活动」"
    # 列表顺序也没变 (老的那个没被顶上来)
    assert [t.thread_id for t in await threads.list_active_with_messages(tenant)] == [
        newer.thread_id,
        older.thread_id,
    ]


async def test_a_user_renamed_title_is_never_overwritten_by_the_auto_title(
    db: PgDatabase,
):
    """用户改过名之后, **自动标题再也盖不上来** (ticket 20 点名要钉住的那条).

    自动标题取自首条用户消息, 只在**标题还空着**时写一次 (`set_title` 的 WHERE 里
    有 `title == ''`). 少了那个条件, 用户起的名字会在下一轮问答收尾时被悄悄换回第
    一句话 —— 而那正是"优化成每次写入都刷标题"的典型后果.
    """
    threads = ThreadsRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    thread = await _thread_with_a_visible_message(db, tenant_id=tenant, user_id="u-1")

    assert await threads.update_title(
        thread.thread_id, "我的退换货问题", tenant_id=tenant, user_id="u-1"
    )
    # 记录员收尾那一拍会调的自动标题: 这里该**什么也不做**
    assert not await threads.set_title(thread.thread_id, "随手打的第一句话")

    reloaded = await threads.get(thread.thread_id)
    assert reloaded is not None
    assert reloaded.title == "我的退换货问题"


async def test_the_three_write_methods_are_scoped_to_the_owner(db: PgDatabase):
    """改标题 / 置顶 / 删除都只认自己的会话 —— 别人的编号一律改不动.

    与列会话那两条 (读) 同一条纪律, 但**写**更要紧: 读错了只是看见别人的, 写错了
    是改了别人的. 判据在仓储这一层 (三个方法都把归属写进 WHERE), 于是上层哪个入口
    漏了校验也改不动别人的东西.
    """
    threads = ThreadsRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    theirs = await _thread_with_a_visible_message(db, tenant_id=tenant, user_id="u-2")
    other_tenant = await _thread_with_a_visible_message(
        db, tenant_id=f"tenant-{uuid4().hex}", user_id="u-1"
    )

    # 同一个租户里的另一个人: 三个动作一个也落不下去
    assert not await threads.update_title(
        theirs.thread_id, "抢过来", tenant_id=tenant, user_id="u-1"
    )
    assert not await threads.set_pinned(
        theirs.thread_id, True, tenant_id=tenant, user_id="u-1"
    )
    assert not await threads.soft_delete(
        theirs.thread_id, tenant_id=tenant, user_id="u-1"
    )
    untouched = await threads.get(theirs.thread_id)
    assert untouched is not None
    assert (untouched.title, untouched.pinned_at, untouched.deleted_at) == (
        "聊过",
        None,
        None,
    )

    # 换一个租户也一样: 同一个人在别的租户里不是这段会话的属主
    assert not await threads.soft_delete(
        other_tenant.thread_id, tenant_id=tenant, user_id="u-1"
    )
    assert (await threads.get(other_tenant.thread_id)).deleted_at is None


async def test_search_looks_at_the_title_only(db: PgDatabase):
    """搜索只搜**标题**: 正文里有这个词不算命中.

    判据收窄到标题 (ticket 25). 更早那一版是「标题或正文」, 代价是搜出来的
    东西说不通: 列表按 `updated_at` 排, 而命中正文的会话常常排在一个标题
    更相关的会话前面 —— 顺序与"哪个更像"无关. 收窄之后搜到的每一条都能在
    标题里看见那个词: 不是能力变小, 是可预测.

    大小写仍不敏感 (`ILIKE`); 通配符照旧按字面搜, 那条另有专门用例.
    """
    threads = ThreadsRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    by_title = await _thread_titled(
        db, tenant_id=tenant, user_id="u-1", title="Retry 退款要几天", body="随便说"
    )
    await _thread_titled(
        db, tenant_id=tenant, user_id="u-1", title="别的", body="我的 Refund 到账了吗"
    )
    await _thread_titled(
        db, tenant_id=tenant, user_id="u-1", title="别的", body="跟这个词无关"
    )

    async def found(query: str) -> set[str]:
        rows = await threads.list_active_with_messages(
            tenant, user_id="u-1", query=query
        )
        return {t.thread_id for t in rows}

    assert await found("退款") == {by_title.thread_id}
    assert await found("retry") == {by_title.thread_id}, "标题里是 Retry, 小写该搜到"
    assert await found("RETRY") == {by_title.thread_id}, "大写同理"
    assert await found("refund") == set(), "正文里有 Refund 不算命中"
    assert await found("没这个词") == set()


async def test_search_stays_inside_the_tenant_and_honours_the_limit(db: PgDatabase):
    """搜索只在这条 (租户, 属主) 的会话里找, 且 `limit` 照样生效.

    搜索是列表的一个过滤条件, 所以它继承列表那两条边界: 看不见别人的会话, 也不会
    因为「搜出来的少」就把 limit 顶掉.
    """
    threads = ThreadsRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    mine = await _thread_titled(
        db, tenant_id=tenant, user_id="u-1", title="退款", body="甲"
    )
    await _thread_titled(db, tenant_id=tenant, user_id="u-2", title="退款", body="乙")
    await _thread_titled(
        db, tenant_id=f"tenant-{uuid4().hex}", user_id="u-1", title="退款", body="丙"
    )

    scoped = await threads.list_active_with_messages(
        tenant, user_id="u-1", query="退款"
    )
    assert [t.thread_id for t in scoped] == [mine.thread_id]

    assert (
        await threads.list_active_with_messages(
            tenant, user_id="u-1", query="退款", limit=0
        )
        == []
    )


async def test_search_treats_wildcards_as_plain_text(db: PgDatabase):
    """`%` 与 `_` 在搜索词里是**字面字符**, 不是 LIKE 的通配符.

    不转义的话, 搜一个下划线会命中**所有**会话 (它是「任意一个字符」) —— 看着像
    搜索坏了, 而那个键恰恰很容易被敲进去.
    """
    threads = ThreadsRepository(db)
    tenant = f"tenant-{uuid4().hex}"
    literal = await _thread_titled(
        db, tenant_id=tenant, user_id="u-1", title="打折 50% 怎么算", body="甲"
    )
    await _thread_titled(
        db, tenant_id=tenant, user_id="u-1", title="别的标题", body="乙"
    )

    async def found(query: str) -> set[str]:
        rows = await threads.list_active_with_messages(
            tenant, user_id="u-1", query=query
        )
        return {t.thread_id for t in rows}

    assert await found("50%") == {literal.thread_id}
    assert await found("_") == set(), "一个下划线不该把所有会话都搜出来"
    assert await found("别_标题") == set(), "下划线不该当「任意一个字符」用"
