"""会话记录员 (ConversationRecorder): 一次运行收尾写什么、写失败怎么办.

被测的是**记录员的行为**, 不是 SQL 对不对 —— 那是 `test_db_store.py` 的 pg_db
用例的事 (真库跑得通). 于是这里用 `tests/doubles.py` 那个共用的假库 (读写都认,
三个测试文件共用一份), 断的是:

1. **写什么**: 一轮正常问答 = 1 条会话行 (懒创建) + 1 条运行行 + N 条消息行
   (可见的与隐藏的一起); 取消 / 失败那一轮也要记, 别让那一轮凭空消失.
2. **状态怎么落**: 运行行直接以终态落库 (`finished_at` 一起写上, 不留自相矛盾
   的行), 会话的 `updated_at` 每轮都刷.
3. **写不进去怎么办** (本文件最要紧的一批): 不抛、记日志、留内存标记, 下一次成功
   写入时**先补一条可见的提示行**再写本轮 —— 用户至少看得见「这里少了一轮」.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest
from doubles import BrokenRecordDatabase, FakeRecordDatabase, record_thread

from CharAgent.agent import LoopOutcome
from CharAgent.agent.utils.types import LoopResult
from CharAgent.db.conversation import TranscriptLine
from CharAgent.db.entities import MessageRole, RunStatus, ThreadStatus
from CharAgent.db.errors import DataStoreError
from CharAgent.db.recorder import (
    MISSED_TURN_TEXT,
    TITLE_LIMIT,
    UNFINISHED_TURN_TEXT,
    ConversationRecorder,
    title_for,
)
from CharAgent.model import FinishReason

THREAD_ID = "toy:u-9f3a:chat-1"

# 「订单到哪了 + 答好了」这一问一答的最小历史 (多数用例都用它)
TWO_LINES = [
    {"role": "user", "content": "订单到哪了"},
    {"role": "assistant", "content": "答好了"},
]


class TwitchyDatabase(FakeRecordDatabase):
    """抖一下的库: 头 `fail_times` 次进事务就抛, 之后正常.

    用来走通「这一轮没记上 → 下一轮补提示行」这条路 —— 它必须发生在**同一个**
    记录员身上 (那个标记活在记录员的内存里, 见 db/recorder.py).
    """

    def __init__(self, *, fail_times: int) -> None:
        super().__init__()
        self._left = fail_times

    @asynccontextmanager
    async def connect(self):
        if self._left > 0:
            self._left -= 1
            raise DataStoreError("抖了一下")
        yield self.session


def recorder(database) -> ConversationRecorder:
    """造一个绑在玩具属主上的记录员."""
    return ConversationRecorder(database=database, tenant_id="toy", user_id="u-9f3a")


def result(
    *,
    content: str | None = "答好了",
    outcome: LoopOutcome = LoopOutcome.FINISHED,
    messages: list | None = None,
    turn_count: int = 1,
    total_tokens: int = 42,
) -> LoopResult:
    """造一个 LoopResult (记录的全部内容都由它派生)."""
    return LoopResult(
        messages=TWO_LINES if messages is None else messages,
        content=content,
        finish_reason=FinishReason.STOP,
        outcome=outcome,
        turns=[],
        turn_count=turn_count,
        total_tokens=total_tokens,
    )


# ---------------------------------------------------------------------------
# 写什么
# ---------------------------------------------------------------------------


async def test_a_normal_turn_writes_a_thread_a_run_and_two_messages() -> None:
    """一轮正常问答: 1 条会话行 + 1 条运行行 + 2 条可见消息 (问答各一条)."""
    database = FakeRecordDatabase()

    written = await recorder(database).record(thread_id=THREAD_ID, result=result())

    assert written is True
    [thread] = database.rows_of("charagent_threads")
    assert thread["thread_id"] == THREAD_ID
    assert (thread["tenant_id"], thread["user_id"]) == ("toy", "u-9f3a"), (
        "属主身份在装配时就绑好了"
    )
    assert thread["title"] == "订单到哪了", "标题取首条用户消息"
    assert thread["status"] == ThreadStatus.ACTIVE.value

    [run] = database.rows_of("charagent_runs")
    assert run["thread_id"] == THREAD_ID
    assert (run["turn_count"], run["total_tokens"]) == (1, 42)
    assert run["status"] == RunStatus.FINISHED.value
    assert run["finished_at"] is not None, "终态的行不该留着空的结束时刻"

    messages = database.rows_of("charagent_messages")
    assert [(row["role"], row["hidden"]) for row in messages] == [
        (MessageRole.USER.value, False),
        (MessageRole.ASSISTANT.value, False),
    ]
    assert messages[1]["content"] == "答好了"
    # 顺序靠 created_at 的微秒偏移保证 (同一批插入没有别的排序依据)
    assert messages[0]["created_at"] < messages[1]["created_at"]
    # 这几行属于刚建的那次执行 (审计按它把「哪一次问答产生的」对上)
    assert {row["run_id"] for row in messages} == {run["run_id"]}


async def test_the_hidden_work_of_a_tool_turn_is_recorded_as_hidden() -> None:
    """工具轮与工具回填进 `hidden=True` (审计查得到, 前端看不见)."""
    database = FakeRecordDatabase()
    wire = [
        {"role": "user", "content": "订单到哪了"},
        {
            "role": "assistant",
            "content": "让我先查一下",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "query_order", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_0", "content": "已发货"},
        {"role": "assistant", "content": "已发货, 明天到"},
    ]

    await recorder(database).record(
        thread_id=THREAD_ID, result=result(content="已发货, 明天到", messages=wire)
    )

    messages = database.rows_of("charagent_messages")
    assert [(row["role"], row["hidden"]) for row in messages] == [
        ("user", False),
        ("assistant", True),
        ("tool", True),
        ("assistant", False),
    ]
    # 中间轮那条 assistant 发起了哪次调用也记下来了 (工具调用表按它对账)
    assert messages[1]["tool_call_ids"] == ["call_0"]


async def test_a_run_that_gave_no_answer_gets_a_notice_line() -> None:
    """跑完却没有可见答复 (guard 刹车): 补一条「这一轮没答完」, 别让那一轮消失."""
    database = FakeRecordDatabase()
    wire = [{"role": "user", "content": "订单到哪了"}]

    await recorder(database).record(
        thread_id=THREAD_ID,
        result=result(content=None, outcome=LoopOutcome.MAX_TURNS, messages=wire),
    )

    messages = database.rows_of("charagent_messages")
    assert [(row["role"], row["hidden"], row["content"]) for row in messages] == [
        ("user", False, "订单到哪了"),
        ("system", False, UNFINISHED_TURN_TEXT),
    ]
    [run] = database.rows_of("charagent_runs")
    assert run["status"] == RunStatus.FINISHED.value, (
        "刹车算正常收尾 (state.py 的映射): 「没答完」说的是答复不在, 不是运行失败"
    )


async def test_only_what_this_run_added_is_written() -> None:
    """`since` 之前的那段不再写第二遍 (重写会写出重复行)."""
    database = FakeRecordDatabase()
    wire = [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
        {"role": "assistant", "content": "第二答"},
    ]

    await recorder(database).record(
        thread_id=THREAD_ID, result=result(content="第二答", messages=wire), since=2
    )

    messages = database.rows_of("charagent_messages")
    assert [row["content"] for row in messages] == ["第二问", "第二答"]


# ---------------------------------------------------------------------------
# 会话行懒创建 + 活动时刻
# ---------------------------------------------------------------------------


async def test_a_thread_owned_by_somebody_else_is_flagged_not_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """会话行已经属于别人时留一笔 warning —— 但**照样写**.

    会话编号是快照与记录**共用的分区键**: 同一段编号换了属主 (业务把两段对话编到了
    同一个键上, 比如命令行拿 `--conversation-id web` 打字面撞上了网页端), 记录就会
    串在一起. 框架不替业务决定该不该写 (不写就是静默丢记录), 但要让这件事看得见.
    """
    database = FakeRecordDatabase(
        threads=[
            record_thread(
                THREAD_ID, tenant_id="别的租户", user_id="别人", title="别人的"
            )
        ]
    )

    with caplog.at_level(logging.WARNING, logger="charagent.db"):
        written = await recorder(database).record(thread_id=THREAD_ID, result=result())

    assert written is True, "照样写: 不写就是静默丢一轮记录"
    assert "已经属于" in caplog.text
    assert len(database.rows_of("charagent_threads")) == 0, "不会另建一行"


async def test_the_thread_is_created_once_and_reused_afterwards() -> None:
    """会话行懒创建: 第一次写入才建, 之后复用 (不再建第二条)."""
    database = FakeRecordDatabase()
    rec = recorder(database)

    await rec.record(thread_id=THREAD_ID, result=result())
    await rec.record(thread_id=THREAD_ID, result=result())

    assert len(database.rows_of("charagent_threads")) == 1


async def test_a_long_question_is_squashed_into_a_one_line_title() -> None:
    """标题是**一行**: 换行压平 + 超长截断 (前端左栏一行放不下整段话)."""
    database = FakeRecordDatabase()
    long_question = "第一行\n第二行\n" + "很长" * TITLE_LIMIT

    await recorder(database).record(
        thread_id=THREAD_ID,
        result=result(
            messages=[
                {"role": "user", "content": long_question},
                {"role": "assistant", "content": "好"},
            ]
        ),
    )

    [thread] = database.rows_of("charagent_threads")
    assert "\n" not in thread["title"]
    assert len(thread["title"]) == TITLE_LIMIT


async def test_every_turn_refreshes_the_thread_activity_time() -> None:
    """每轮都刷一次会话的活动时刻 —— 会话列表按它倒序排."""
    database = FakeRecordDatabase()
    rec = recorder(database)

    await rec.record(thread_id=THREAD_ID, result=result())
    await rec.record(thread_id=THREAD_ID, result=result())

    assert database.session.updates == ["charagent_threads"] * 2


# ---------------------------------------------------------------------------
# 取消 / 失败那一轮
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [RunStatus.CANCELLED, RunStatus.FAILED])
async def test_an_unfinished_turn_is_recorded_with_its_question(
    status: RunStatus,
) -> None:
    """取消 / 失败那一轮: 提问还在, 加一条可见的「这一轮没答完」+ 对应状态."""
    database = FakeRecordDatabase()

    written = await recorder(database).record_unfinished(
        thread_id=THREAD_ID, question="订单到哪了", status=status
    )

    assert written is True
    messages = database.rows_of("charagent_messages")
    assert [(row["role"], row["hidden"], row["content"]) for row in messages] == [
        ("user", False, "订单到哪了"),
        ("system", False, UNFINISHED_TURN_TEXT),
    ]
    [run] = database.rows_of("charagent_runs")
    assert run["status"] == status.value
    assert run["finished_at"] is not None


async def test_an_unfinished_turn_still_creates_the_thread() -> None:
    """第一句话就被取消: 会话行照样建起来 (否则那句提问没有落点, 外键会拦)."""
    database = FakeRecordDatabase()

    await recorder(database).record_unfinished(
        thread_id=THREAD_ID, question="第一句就被打断", status=RunStatus.CANCELLED
    )

    [thread] = database.rows_of("charagent_threads")
    assert thread["title"] == "第一句就被打断"


# ---------------------------------------------------------------------------
# 写不进去怎么办 (本文件最要紧的一批)
# ---------------------------------------------------------------------------


async def test_a_failed_write_is_logged_and_marked_instead_of_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """库连不上: 返回 False + 记一笔 warning, **不抛** (这一句问话不能被它拦下)."""
    rec = recorder(BrokenRecordDatabase())

    with caplog.at_level(logging.WARNING, logger="charagent.db"):
        written = await rec.record(thread_id=THREAD_ID, result=result())

    assert written is False
    assert rec.missed_threads == frozenset({THREAD_ID})
    assert "没能记进记录表" in caplog.text


async def test_the_next_write_first_adds_a_visible_notice() -> None:
    """上一轮没记上: 下一轮成功写入时**先补一条可见提示行**再写本轮."""
    database = TwitchyDatabase(fail_times=1)
    rec = recorder(database)

    assert await rec.record(thread_id=THREAD_ID, result=result()) is False
    assert rec.missed_threads == frozenset({THREAD_ID})
    assert await rec.record(thread_id=THREAD_ID, result=result()) is True

    messages = database.rows_of("charagent_messages")
    assert [row["content"] for row in messages] == [
        MISSED_TURN_TEXT,
        "订单到哪了",
        "答好了",
    ]
    assert messages[0]["role"] == "system"
    assert messages[0]["hidden"] is False, "提示行是给用户看的"
    assert rec.missed_threads == frozenset(), "补过一次就不再欠着"


async def test_the_notice_is_added_only_once_per_missed_turn() -> None:
    """补过之后不再重复补: 后面几轮写出来的是平常那两行."""
    database = TwitchyDatabase(fail_times=1)
    rec = recorder(database)

    await rec.record(thread_id=THREAD_ID, result=result())
    await rec.record(thread_id=THREAD_ID, result=result())
    await rec.record(thread_id=THREAD_ID, result=result())

    contents = [row["content"] for row in database.rows_of("charagent_messages")]
    assert contents == [
        MISSED_TURN_TEXT,
        "订单到哪了",
        "答好了",
        "订单到哪了",
        "答好了",
    ]


# ---------------------------------------------------------------------------
# 纯函数: 标题
# ---------------------------------------------------------------------------


def test_title_comes_from_the_first_user_message() -> None:
    """标题取这批行里第一条用户消息 —— 不是第一条行 (那可能是条 system 说明)."""
    lines = [
        TranscriptLine(role="system", content=MISSED_TURN_TEXT, reasoning=None),
        TranscriptLine(role="user", content="  问  一句  ", reasoning=None),
    ]

    assert title_for(lines) == "问 一句"


def test_title_is_empty_when_there_is_no_user_message() -> None:
    """没有用户消息时给空串 (``add`` 的约定: 空串 = 还没起名)."""
    assert title_for([]) == ""


async def test_a_fresh_summary_is_recorded_as_a_hidden_line() -> None:
    """这一轮新压出来的摘要进 hidden=True (审计看得出「模型当时看到什么」).

    摘要不是账本里的消息 (压缩只是给模型的视图), 所以它由记录员单独补一行; 而
    「新不新」由会话判 (见 ChatSession._run) —— 这里只断「给了就记, 记成隐藏的」.
    """
    database = FakeRecordDatabase()

    await recorder(database).record(
        thread_id=THREAD_ID, result=result(), summary="早前聊的是查订单"
    )

    messages = database.rows_of("charagent_messages")
    assert [(row["role"], row["hidden"]) for row in messages] == [
        ("user", False),
        ("assistant", False),
        ("system", True),
    ]
    assert messages[2]["content"] == "早前聊的是查订单"


async def test_without_a_fresh_summary_no_line_is_added() -> None:
    """摘要没变 (默认 None) 就不加行 —— 否则每轮都写一条一模一样的."""
    database = FakeRecordDatabase()

    await recorder(database).record(thread_id=THREAD_ID, result=result())

    assert len(database.rows_of("charagent_messages")) == 2
