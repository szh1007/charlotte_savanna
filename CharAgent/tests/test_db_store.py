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

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from conftest import sqlalchemy_test_url
from sqlalchemy import create_engine, pool, text

from CharAgent.db import (
    DataStoreError,
    MessagesRepository,
    PgDatabase,
    RunsRepository,
    RunStatus,
    ThreadsRepository,
    ToolCallsRepository,
    ToolCallStatus,
    build_tool_call,
    conversation_turns,
    visible_transcript,
)
from CharAgent.db.schema import TABLE_NAMES

pytestmark = pytest.mark.pg_db

TEST_SCHEMA = "charagent_test"


@pytest.fixture(scope="session")
def _admin_url():
    """连到默认 schema 的 URL (用来建 / 删测试 schema)."""
    return sqlalchemy_test_url()


def _run_sql(url, *statements: str) -> None:
    """同步跑几条 SQL (放进线程里执行, 别卡事件循环).

    用 NullPool: 每次跑完就关连接 —— 建/删 schema 是低频动作, 留着池子没意义.
    """
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def db(_admin_url) -> AsyncIterator[PgDatabase]:
    """指向测试 schema 的 PgDatabase, 用完把测试 schema 整个删掉.

    **整个 schema 删掉**是最干净的收尾: 表、索引、外键全没了, 不留任何痕迹,
    也不担心漏删哪张.
    """
    await asyncio.to_thread(
        _run_sql,
        _admin_url,
        f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE",
        f"CREATE SCHEMA {TEST_SCHEMA}",
    )
    # 引擎要 SQLAlchemy 的 URL (带 +psycopg 驱动名), 而 `_run_sql` 那条路走
    # psycopg 需要的文本形式 —— 两种形式各用各的, 别混
    engine = create_engine(
        sqlalchemy_test_url(),
        # 每条连接都先切到测试 schema —— 仓储与迁移写的 SQL 里都不带 schema 前缀,
        # 于是它们自然落在隔离区里
        connect_args={"options": f"-csearch_path={TEST_SCHEMA}"},
    )
    database = PgDatabase(engine=engine)
    # 先把表建好: 这是每个用例的起跑线 (刚删过 schema, 表一定是没有的).
    # 查表建表这件事本身另有用例专门验 (test_create_tables_is_idempotent).
    await database.create_tables()
    try:
        yield database
    finally:
        await asyncio.to_thread(engine.dispose)
        await asyncio.to_thread(
            _run_sql, _admin_url, f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"
        )


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
