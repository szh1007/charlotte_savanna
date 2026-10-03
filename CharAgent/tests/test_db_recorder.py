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
from decimal import Decimal
from typing import Any

import pytest
from doubles import (
    BrokenRecordDatabase,
    FakeRecordDatabase,
    RecordingSleep,
    record_thread,
)
from mock_llm import (
    MockLLM,
    make_tool_call,
    text_response,
    tool_call_response,
)

from CharAgent.agent import AgentLoop, LoopOutcome
from CharAgent.agent.utils.types import (
    LoopResult,
    ToolCallFact,
    ToolCallOutcome,
    TurnRecord,
)
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.db.conversation import TranscriptLine
from CharAgent.db.cost import CostGap, PriceTable
from CharAgent.db.entities import MessageRole, RunStatus, ThreadStatus
from CharAgent.db.errors import DataStoreError
from CharAgent.db.recorder import (
    MISSED_TURN_TEXT,
    TITLE_LIMIT,
    UNFINISHED_TURN_TEXT,
    ConversationRecorder,
    title_for,
)
from CharAgent.db.repositories.tool_calls import ToolCallsRepository
from CharAgent.model import FinishReason
from CharAgent.model.utils.errors import ModelStatusError
from CharAgent.model.utils.types import ModelResponse, Usage
from CharAgent.retry import (
    CircuitPolicy,
    FailoverChatModel,
    RetryingChatModel,
    RetryPolicy,
)
from CharAgent.tool import ToolActionableError, tool

THREAD_ID = "toy:u-9f3a:chat-1"

# 「订单到哪了 + 答好了」这一问一答的最小历史 (多数用例都用它)
TWO_LINES = [
    {"role": "user", "content": "订单到哪了"},
    {"role": "assistant", "content": "答好了"},
]

# 「我要去查一下」那一条 assistant 消息 (wire 形状: 带 tool_calls, 正文为空)
ORDER_CALL = '{"order_no": "SF123"}'
ASSISTANT_CALL = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {
            "id": "call_0",
            "type": "function",
            "function": {"name": "query_order", "arguments": ORDER_CALL},
        }
    ],
}
TOOL_FILL = {"role": "tool", "tool_call_id": "call_0", "content": "已发货"}


# 端到端用例的两个载体工具 (一个成功、一个自己抛可操作错误)
@tool
def query_order(
    order_no: str,
) -> str:
    """查订单状态.

    Args:
        order_no: 订单号.
    """
    return "已发货"


@tool
def request_refund(
    order_no: str,
) -> str:
    """申请退款.

    Args:
        order_no: 订单号.
    """
    raise ToolActionableError(f"订单号 {order_no!r} 格式不对: 应当是 14 位数字")


query_order_tool = query_order
request_refund_tool = request_refund


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
    turns: list[TurnRecord] | None = None,
    turn_count: int = 1,
    total_tokens: int = 42,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    cache_hit_tokens: int | None = None,
    cache_miss_tokens: int | None = None,
    prompt_ref: dict[str, str] | None = None,
) -> LoopResult:
    """造一个 LoopResult (记录的全部内容都由它派生).

    用量分解与引用默认不给 (None): 多数用例只关心「消息与状态写对了吗」, 那两样在
    各自的用例里显式给 —— 它们的 None 与 0 意思不同, 不该由这个工厂替用例决定.
    """
    return LoopResult(
        messages=TWO_LINES if messages is None else messages,
        content=content,
        finish_reason=FinishReason.STOP,
        outcome=outcome,
        turns=[] if turns is None else turns,
        turn_count=turn_count,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        prompt_ref=prompt_ref,
    )


async def record_turn(
    rec: ConversationRecorder, *, thread_id: str = THREAD_ID, **kwargs: Any
) -> bool:
    """走完一次运行的两拍: 先开账 (`begin`), 再落定 (`record`).

    记录员从 ticket 22 起是两段式的 (编号在跑之前就定下来, 帧才盖得上), 而多数用例
    关心的是「收尾那一下写了什么」—— 于是这两拍在测试里也收成一处, 免得每个用例
    自己拼. 需要检查 `begin` 本身 (失败 / 编号) 的用例直接用那两个方法.
    """
    run_id = await rec.begin(thread_id=thread_id, title=kwargs.pop("title", ""))
    return await rec.record(thread_id=thread_id, run_id=run_id, **kwargs)


async def record_unfinished_turn(
    rec: ConversationRecorder,
    *,
    question: str,
    status: RunStatus,
    thread_id: str = THREAD_ID,
    **kwargs: Any,
) -> bool:
    """同上, 走「没答完」那一拍 (标题就是那句提问 —— 会话行的标题按首条用户消息定)."""
    run_id = await rec.begin(thread_id=thread_id, title=question)
    return await rec.record_unfinished(
        thread_id=thread_id, run_id=run_id, question=question, status=status, **kwargs
    )


# ---------------------------------------------------------------------------
# 写什么
# ---------------------------------------------------------------------------


async def test_a_normal_turn_writes_a_thread_a_run_and_two_messages() -> None:
    """一轮正常问答: 1 条会话行 + 1 条运行行 + 2 条可见消息 (问答各一条)."""
    database = FakeRecordDatabase()

    written = await record_turn(recorder(database), result=result())

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


async def test_the_run_row_carries_the_usage_breakdown_and_the_prompt_version() -> None:
    """运行行带上用量分解 (#34 归因) 与提示词版本 (#40 版本化).

    这两样都是「顺手带上」的: 分解就在 `LoopResult` 上, 提示词版本就在它带着的引用
    里 —— 而在此之前 `prompt_version` 那一列从建表起就一直是 NULL.
    """
    database = FakeRecordDatabase()

    written = await record_turn(
        recorder(database),
        result=result(
            total_tokens=4441,
            input_tokens=4120,
            output_tokens=321,
            reasoning_tokens=180,
            cache_hit_tokens=2048,
            cache_miss_tokens=2072,
            prompt_ref={"name": "system/v2", "sha256": "0" * 64},
        ),
    )

    assert written is True
    [run] = database.rows_of("charagent_runs")
    assert run["prompt_version"] == "system/v2", "取的是引用里的名字, 不是哈希"
    assert (run["input_tokens"], run["output_tokens"]) == (4120, 321)
    assert run["reasoning_tokens"] == 180
    assert (run["cache_hit_tokens"], run["cache_miss_tokens"]) == (2048, 2072)
    # 分解与总量对得上 —— 它们是同一个 usage 的两种记法
    assert run["input_tokens"] + run["output_tokens"] == run["total_tokens"]
    assert run["cache_hit_tokens"] + run["cache_miss_tokens"] == run["input_tokens"]


async def test_an_unfinished_turn_leaves_the_usage_breakdown_empty() -> None:
    """没跑完那一轮: 分解留空 —— NULL 是「没有」, 而 0 是「确实是零」."""
    database = FakeRecordDatabase()

    await record_unfinished_turn(
        recorder(database), question="订单到哪了", status=RunStatus.CANCELLED
    )

    [run] = database.rows_of("charagent_runs")
    assert run["input_tokens"] is None
    assert run["cache_miss_tokens"] is None
    assert run["prompt_version"] is None
    assert run["model"] is None, "调用方没给模型名就留空, 不编一个名字出来"


async def test_the_run_row_carries_the_model_name_on_both_paths() -> None:
    """运行行带上模型名 (#40 版本归因): 跑完的那一轮与没跑完的那一轮都带得上.

    模型名与用量分解不是一回事: 后者是**跑出来的账目** (没跑完就没有), 前者是跑
    之前就定下的**配置事实** —— 所以取消 / 失败那一行照样写得出来, 而那一行的
    账目仍然是空的. 归因要能答的问题是「这次用的是哪个模型」, 与「跑成没跑成」
    无关.
    """
    finished = FakeRecordDatabase()

    await record_turn(recorder(finished), result=result(), model="deepseek-flash")

    [run] = finished.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash"

    unfinished = FakeRecordDatabase()

    await record_unfinished_turn(
        recorder(unfinished),
        question="订单到哪了",
        status=RunStatus.CANCELLED,
        model="deepseek-flash",
    )

    [run] = unfinished.rows_of("charagent_runs")
    assert (run["model"], run["input_tokens"]) == ("deepseek-flash", None), (
        "模型名有, 账目空 —— 两者不是一回事"
    )


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

    await record_turn(
        recorder(database), result=result(content="已发货, 明天到", messages=wire)
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

    await record_turn(
        recorder(database),
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

    await record_turn(
        recorder(database), result=result(content="第二答", messages=wire), since=2
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
        written = await record_turn(recorder(database), result=result())

    assert written is True, "照样写: 不写就是静默丢一轮记录"
    assert "已经属于" in caplog.text
    assert database.written_rows_of("charagent_threads") == [], "不会另建一行"


async def test_the_thread_is_created_once_and_reused_afterwards() -> None:
    """会话行懒创建: 第一次写入才建, 之后复用 (不再建第二条)."""
    database = FakeRecordDatabase()
    rec = recorder(database)

    await record_turn(rec, result=result())
    await record_turn(rec, result=result())

    assert len(database.rows_of("charagent_threads")) == 1


async def test_a_long_question_is_squashed_into_a_one_line_title() -> None:
    """标题是**一行**: 换行压平 + 超长截断 (前端左栏一行放不下整段话)."""
    database = FakeRecordDatabase()
    long_question = "第一行\n第二行\n" + "很长" * TITLE_LIMIT

    await record_turn(
        recorder(database),
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
    """每轮都刷一次会话的活动时刻 —— 会话列表按它倒序排.

    断的是**效果** (那个时刻真的往后走了), 不是「发了几条 UPDATE」: ticket 22 起
    一轮里有三笔写 (开账的行 / 补标题 / 刷时刻), 数语句已经说明不了「刷没刷」.
    """
    database = FakeRecordDatabase()
    rec = recorder(database)

    await record_turn(rec, result=result())
    first = database.rows_of("charagent_threads")[0]["updated_at"]
    await record_turn(rec, result=result())
    second = database.rows_of("charagent_threads")[0]["updated_at"]

    assert second > first, "第二轮之后活动时刻要比第一轮晚"


# ---------------------------------------------------------------------------
# 取消 / 失败那一轮
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [RunStatus.CANCELLED, RunStatus.FAILED])
async def test_an_unfinished_turn_is_recorded_with_its_question(
    status: RunStatus,
) -> None:
    """取消 / 失败那一轮: 提问还在, 加一条可见的「这一轮没答完」+ 对应状态."""
    database = FakeRecordDatabase()

    written = await record_unfinished_turn(
        recorder(database), question="订单到哪了", status=status
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

    await record_unfinished_turn(
        recorder(database), question="第一句就被打断", status=RunStatus.CANCELLED
    )

    [thread] = database.rows_of("charagent_threads")
    assert thread["title"] == "第一句就被打断"


# ---------------------------------------------------------------------------
# 写不进去怎么办 (本文件最要紧的一批)
# ---------------------------------------------------------------------------


async def test_a_failed_write_is_logged_and_marked_instead_of_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """库连不上: 返回 False + 记一笔 warning, **不抛** (这一句问话不能被它拦下).

    日志**只有一条** (ticket 22 起): 失败发生在开账那一拍 (`begin`), 它已经记过;
    收尾那拍只留「这段会话欠着一条提示行」的标记, 不再重复报 —— 同一轮报两条只会
    稀释信号.
    """
    rec = recorder(BrokenRecordDatabase())

    with caplog.at_level(logging.WARNING, logger="charagent.db"):
        written = await record_turn(rec, result=result())

    assert written is False
    assert rec.missed_threads == frozenset({THREAD_ID})
    assert "没能开出来" in caplog.text
    assert caplog.text.count("WARNING") == 1, "同一轮只该有一条告警"


async def test_the_next_write_first_adds_a_visible_notice() -> None:
    """上一轮没记上: 下一轮成功写入时**先补一条可见提示行**再写本轮."""
    database = TwitchyDatabase(fail_times=1)
    rec = recorder(database)

    assert await record_turn(rec, result=result()) is False
    assert rec.missed_threads == frozenset({THREAD_ID})
    assert await record_turn(rec, result=result()) is True

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

    await record_turn(rec, result=result())
    await record_turn(rec, result=result())
    await record_turn(rec, result=result())

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

    await record_turn(recorder(database), result=result(), summary="早前聊的是查订单")

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

    await record_turn(recorder(database), result=result())

    assert len(database.rows_of("charagent_messages")) == 2


# ---------------------------------------------------------------------------
# 产生即落库 (ticket 27): 运行中途那两拍 + 收尾补齐 + 修订
# ---------------------------------------------------------------------------
#
# 记录层从这一片起是「产生即落库」: 运行中途那两拍 (工具执行前 / 每轮收尾) 把刚
# 产生的写进去, 而收尾那一拍 (`record` / `record_unfinished`) 变成**补齐 + 修订**.
# 于是同一批行会被写两次 —— 编号由 (run_id, 下标) 派生, 两次落在同一行上.


def fact(
    *,
    index: int = 1,
    call_id: str = "call_0",
    name: str = "query_order",
    arguments: str = ORDER_CALL,
    outcome: ToolCallOutcome = ToolCallOutcome.PENDING,
    result: str | None = None,
    duration_ms: int | None = None,
    approval_prompt: str = "",
    approval_needs: tuple[str, ...] = (),
) -> ToolCallFact:
    """造一条工具调用事实 (用例只关心其中一两项时, 其余给合理默认)."""
    return ToolCallFact(
        message_index=index,
        tool_call_id=call_id,
        tool_name=name,
        arguments=arguments,
        outcome=outcome,
        result=result,
        duration_ms=duration_ms,
        approval_prompt=approval_prompt,
        approval_needs=approval_needs,
    )


async def test_the_flush_writes_what_was_just_produced() -> None:
    """运行中途那一拍: 那几条消息**当场**落库, 编号由 (运行, 下标) 算出来.

    编号算得出来是这一片的地基: 工具调用行拿它当外键 (`message_id`), 而运行中途
    那条 assistant 行正是**发起调用的那一行**.
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")

    await rec.flush(
        thread_id=THREAD_ID,
        run_id=run_id,
        start=1,
        messages=[ASSISTANT_CALL, TOOL_FILL],
    )

    rows = database.rows_of("charagent_messages")
    assert [row["message_id"] for row in rows] == [f"{run_id}:1", f"{run_id}:2"]
    assert [(row["role"], row["hidden"]) for row in rows] == [
        # 发起工具调用的中间轮与工具回填都是内部件 (前端看不见, 审计查得到)
        ("assistant", True),
        ("tool", True),
    ]
    assert rows[0]["tool_call_ids"] == ["call_0"], "它发起了哪次调用也一并记下"
    assert {row["run_id"] for row in rows} == {run_id}


async def test_a_call_is_written_pending_first_then_advanced() -> None:
    """工具调用行两笔: 执行前 `pending`, 执行后回填结论与耗时.

    与 issue 22 在 `runs` 上建立的 begin / finish 同构 —— 挂起那条的中间态因此与
    普通那条走**同一个形状** (两套写法必然漂移).
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")

    await rec.flush(
        thread_id=THREAD_ID,
        run_id=run_id,
        start=1,
        messages=[ASSISTANT_CALL],
        calls=[fact()],
    )
    [row] = database.rows_of("charagent_tool_calls")
    assert (row["status"], row["result"], row["duration_ms"]) == (
        "pending",
        None,
        None,
    ), "还没执行: 没有结论, 也没有耗时 (NULL 与 0 毫秒不是一回事)"
    assert (row["tool_name"], row["arguments"]) == ("query_order", ORDER_CALL)
    assert row["message_id"] == f"{run_id}:1", "挂在发起它的那条 assistant 消息上"

    await rec.flush(
        thread_id=THREAD_ID,
        run_id=run_id,
        start=2,
        messages=[TOOL_FILL],
        calls=[
            fact(outcome=ToolCallOutcome.SUCCEEDED, result="已发货", duration_ms=142)
        ],
    )
    [row] = database.rows_of("charagent_tool_calls")
    assert (row["status"], row["result"], row["duration_ms"]) == (
        "succeeded",
        "已发货",
        142,
    )


async def test_a_suspended_call_is_written_and_waits_for_a_verdict() -> None:
    """挂起那条写 `needs_approval`, 且 `approved_at` 为空 —— 等人来批.

    产生挂起归 issue 34 (它才认 `Decision` 的第三个值); 本片提供的是**这条写入
    路径** —— 而 ADR-0014 的判据 (`status = needs_approval AND approved_at IS NULL`)
    要的就是这一行.
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="帮我退款")

    await rec.flush(
        thread_id=THREAD_ID,
        run_id=run_id,
        start=1,
        messages=[ASSISTANT_CALL],
        calls=[fact(outcome=ToolCallOutcome.NEEDS_APPROVAL)],
    )

    [row] = database.rows_of("charagent_tool_calls")
    assert row["status"] == "needs_approval"
    assert row["approved_at"] is None
    assert row["approved_by"] is None


async def test_writing_the_same_batch_twice_leaves_one_row_each() -> None:
    """幂等: 同一批消息与同一条调用写两次, 库里一条都没多.

    两次写是常态 (运行中途那一拍 + 收尾补齐那一拍), 编号算得出来正是为了它.
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    batch: dict[str, Any] = {
        "thread_id": THREAD_ID,
        "run_id": run_id,
        "start": 1,
        "messages": [ASSISTANT_CALL, TOOL_FILL],
        "calls": [
            fact(outcome=ToolCallOutcome.SUCCEEDED, result="已发货", duration_ms=7)
        ],
    }

    await rec.flush(**batch)
    await rec.flush(**batch)

    assert len(database.rows_of("charagent_messages")) == 2, "两条消息各一行"
    assert len(database.rows_of("charagent_tool_calls")) == 1, "一次调用一行"


async def test_the_finish_pass_fills_in_what_no_turn_wrote() -> None:
    """收尾补齐: 运行中途一条都没写上 (记录员没实现 `flush`), 收尾那一趟补上.

    这一条钉的是**没配增量落库时行为与从前一样** —— 消息与调用都由收尾写出, 而
    编号照样是算出来的 (于是它与「中途写过」那种情形落在同一批行上).
    """
    database = FakeRecordDatabase()
    turn = TurnRecord(
        turn=1,
        response=tool_call_response(
            make_tool_call("query_order", ORDER_CALL, call_id="call_0")
        ),
        messages=[],
        tokens=7,
        elapsed_ms=12.0,
        calls=[
            fact(outcome=ToolCallOutcome.SUCCEEDED, result="已发货", duration_ms=142)
        ],
    )

    written = await record_turn(
        recorder(database),
        result=result(
            content="已发货",
            messages=[
                {"role": "user", "content": "订单到哪了"},
                ASSISTANT_CALL,
                TOOL_FILL,
                {"role": "assistant", "content": "已发货"},
            ],
            turns=[turn],
        ),
    )

    assert written is True
    [run] = database.rows_of("charagent_runs")
    assert [row["message_id"] for row in database.rows_of("charagent_messages")] == [
        f"{run['run_id']}:0",
        f"{run['run_id']}:1",
        f"{run['run_id']}:2",
        f"{run['run_id']}:3",
    ]
    [call] = database.rows_of("charagent_tool_calls")
    assert (call["status"], call["result"], call["duration_ms"]) == (
        "succeeded",
        "已发货",
        142,
    )
    assert call["message_id"] == f"{run['run_id']}:1", (
        "归属还是那条发起调用的 assistant 消息"
    )


async def test_the_finish_pass_revises_a_truncated_answer_into_one_line() -> None:
    """修订: 截断续写的几段在运行中如实各写一行, 收尾把这一轮答复修成**一条**.

    不修的话, 前端会把同一段答案看成被截成两截的两句话 —— 而可见的那条必须是
    `LoopResult.content` (框架拼好的完整正文), 不是消息数组末尾那一段.
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="讲个故事")
    wire = [
        {"role": "user", "content": "讲个故事"},
        {"role": "assistant", "content": "从前有座山"},
        {"role": "system", "content": "接着上面继续写"},
        {"role": "assistant", "content": "山里有座庙"},
    ]

    # 运行中那一拍: 产生时是什么样就写成什么样 (前一段这时也还是可见的)
    await rec.flush(thread_id=THREAD_ID, run_id=run_id, start=0, messages=wire)
    assert [row["hidden"] for row in database.rows_of("charagent_messages")] == [
        False,
        False,
        True,
        False,
    ]

    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(content="从前有座山山里有座庙", messages=wire),
    )

    rows = database.rows_of("charagent_messages")
    assert [row["hidden"] for row in rows] == [False, True, True, False], (
        "早先那一段退成隐藏行 (原文仍在, 审计查得到)"
    )
    assert rows[3]["content"] == "从前有座山山里有座庙", "可见的那条是拼合后的完整正文"
    assert len(rows) == 4, "修订不改行数: 一条不多一条不少"


async def test_a_failed_flush_only_degrades(caplog: pytest.LogCaptureFixture) -> None:
    """运行中途那一拍写不进去: 只记一笔 warning, **不抛**、也不算「缺了一轮」.

    为什么不算缺: 收尾那一趟会补齐 (幂等), 所以此刻没写上不是「记录缺了一行」——
    这时补一条用户可见的提示反而是误报 (最后并不缺).
    """
    rec = recorder(BrokenRecordDatabase())

    with caplog.at_level(logging.WARNING, logger="charagent.db"):
        await rec.flush(
            thread_id=THREAD_ID, run_id="run-1", start=1, messages=[ASSISTANT_CALL]
        )

    assert "没能记进记录表" in caplog.text
    assert rec.missed_threads == frozenset(), "中途那一拍失败不算欠一条提示行"


async def test_a_real_run_ends_up_listable_by_run() -> None:
    """端到端: 一次真跑 (loop + 记录员 + 假库) 之后, 那两次调用都**查得出来**.

    验收第 1 条的自动化版本 (真机那一次是同一件事的手工版): 把轨迹的**来源**
    (loop 交出的事实) 与**落库** (记录员) 接起来, 而 `list_for_run` 正是 issue 28
    的 `trace` 会走的那条读路径. 中间任何一环断了 (下标算错 / 编号对不上 / 外键
    挂错消息), 这条都会红.

    两次调用刻意一成一败 (第二个工具自己抛可操作错误): 「工具失败」也是一次调用,
    同样要留下一行.
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    loop = AgentLoop(
        MockLLM.scripted(
            [
                tool_call_response(
                    make_tool_call("query_order", ORDER_CALL, call_id="call_0")
                ),
                tool_call_response(
                    make_tool_call(
                        "request_refund", '{"order_no": "短"}', call_id="call_1"
                    )
                ),
                text_response("订单已发货; 退款没申请上"),
            ]
        ),
        tools=[query_order_tool, request_refund_tool],
        saver=InMemoryCheckpointSaver(),
        thread_id=THREAD_ID,
        trace_sink=rec,
    )

    result = await loop.run([{"role": "user", "content": "订单到哪了"}], run_id=run_id)
    assert await rec.record(thread_id=THREAD_ID, run_id=run_id, result=result)

    calls = await ToolCallsRepository(database).list_for_run(run_id)
    assert [(call.tool_name, call.status) for call in calls] == [
        ("query_order", "succeeded"),
        ("request_refund", "failed"),
    ], "两次调用各一行, 状态按事实"
    first, second = calls
    assert first.arguments == ORDER_CALL, "参数原样 (不预解析)"
    assert first.result == "已发货"
    assert first.duration_ms is not None
    assert second.result and "格式不对" in second.result, "失败原因就是这一条的结果"
    assert second.duration_ms is not None, "它真的跑过 (失败也是一次执行)"
    # 归属: 两次调用挂在发起它们的那条 assistant 消息行上, 而那行确实在库里
    message_ids = {row["message_id"] for row in database.rows_of("charagent_messages")}
    assert {first.message_id, second.message_id} <= message_ids


async def test_the_unfinished_notice_does_not_take_a_wire_index() -> None:
    """「这一轮没答完」那句说明**不占**派生编号 —— 它没有 wire 下标.

    踩过的坑 (本用例就是为它写的): 一次跑到第二轮才失败的运行, 库里已经有
    `run_id:since+1` 那一行 (第一轮真产生的消息). 说明行若也算成那个编号, 幂等写
    就会把它当成「已经写过了」跳过 —— 用户看不到那句话, 而那一轮看起来**凭空消失**.
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    # 第一轮跑完了 (它产生的那条消息落过库), 第二轮才失败
    await rec.flush(
        thread_id=THREAD_ID,
        run_id=run_id,
        start=1,
        messages=[{"role": "assistant", "content": "答到一半"}],
    )

    await rec.record_unfinished(
        thread_id=THREAD_ID,
        run_id=run_id,
        question="订单到哪了",
        status=RunStatus.FAILED,
        since=0,
    )

    contents = [row["content"] for row in database.rows_of("charagent_messages")]
    assert UNFINISHED_TURN_TEXT in contents, "说明行必须真的落库"
    assert contents.count("订单到哪了") == 1, "提问行不重复写"


# ---------------------------------------------------------------------------
# 续跑段落的记账 (ticket 33): 不收尾的那一种
# ---------------------------------------------------------------------------


async def test_a_segment_that_leaves_the_run_row_alone() -> None:
    """不结账 (`finish_run=False`): 消息照写, 运行行一笔不碰.

    这种情形只出现在 HITL 的续跑段上 —— 那一次运行横跨挂起等待期, 这一段跑完了
    它还没结束, 于是状态 / 账目 / 结束时刻 / 金额全都不能写 (写下去就是替收尾的
    那一段做决定, 而它可能还要再挂起一次).

    为什么消息要照写: 它们记的是这一段**产生的事实** (模型答了什么、调了什么工具),
    与那一次运行结没结账无关 —— 不写的话用户在会话记录里看不到这一段.
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    # 开账那一刻那一行的样子: 状态 running、账目为零、没有结束时刻
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")

    written = await rec.record(
        thread_id=THREAD_ID, result=result(), run_id=run_id, finish_run=False
    )

    assert written is True
    [run] = database.rows_of("charagent_runs")
    assert run["status"] == RunStatus.RUNNING.value, "那一次运行还没结束"
    assert run["finished_at"] is None
    assert run["turn_count"] == 0, "账目也留着 —— 收尾那一段拿到的才是累计值"
    assert run["total_cost"] is None, "没结账就不算钱 (算了也没地方放)"
    messages = database.rows_of("charagent_messages")
    assert [row["role"] for row in messages] == ["user", "assistant"]
    assert {row["run_id"] for row in messages} == {run_id}, "这一段的消息仍属于那次运行"


def _priced_recorder(database: Any) -> ConversationRecorder:
    """绑在玩具属主上、价目表给死的记录员 (两家模型各有一档价)."""
    return ConversationRecorder(
        database=database,
        tenant_id="toy",
        user_id="u-9f3a",
        prices=PriceTable.from_json(
            '{"models": {'
            '"deepseek-flash": {"cache_miss": 2, "cache_hit": 0.5, "output": 8}, '
            '"gpt-6-luna": {"cache_miss": 1, "cache_hit": 0.1, "output": 5}}}'
        ),
    )


async def test_a_run_served_by_two_models_is_recorded_as_both_names() -> None:
    """一趟运行的两段落在一家不同的模型上: 记组合名, 金额随之留空.

    什么时候会发生: HITL 的续跑段 (挂起 -> 人确认 -> 接着跑) 可能落在另一家模型上
    —— 模型层熔断切到了备份 (difficulties #14). 后一段直接覆盖前一段的话, 行上写着
    一个名字、金额却按**它**的单价算了整趟的 token (含前一家答的那些): 一笔对不上
    的账. 合出来的组合名在价目表里查不到, 于是金额留空并写明原因 —— 报不出钱, 也
    不报错钱.
    """
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    usage = {"input_tokens": 100, "cache_miss_tokens": 100, "output_tokens": 10}

    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(**usage),
        model="deepseek-flash",
    )
    [run] = database.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash"
    assert run["total_cost"] is not None, "单模型那一段本来算得出来"

    await rec.record(
        thread_id=THREAD_ID, run_id=run_id, result=result(**usage), model="gpt-6-luna"
    )

    [run] = database.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash+gpt-6-luna", "两段两家的名字合起来"
    assert run["total_cost"] is None, "组合名查不到价 -> 不留一个按错单价算的数"
    assert run["total_cost_detail"]["reason"] == CostGap.NO_PRICE.value
    assert run["total_cost_detail"]["model"] == "deepseek-flash+gpt-6-luna"


def _turn(turn: int, *, model: str, usage: Usage, tokens: int) -> TurnRecord:
    """一轮记录 (只填拆账用得上的那几样: 响应带 usage 与服务方, 轮内 tokens)."""
    return TurnRecord(
        turn=turn,
        response=ModelResponse(
            content="答好了",
            finish_reason=FinishReason.STOP,
            usage=usage,
            model=model,
        ),
        messages=[],
        tokens=tokens,
        elapsed_ms=0.0,
    )


def _usage(*, miss: int, hit: int, out: int) -> Usage:
    return Usage(
        input_tokens=miss + hit,
        output_tokens=out,
        total_tokens=miss + hit + out,
        cache_miss_tokens=miss,
        cache_hit_tokens=hit,
    )


async def test_a_run_that_switched_models_is_priced_per_model() -> None:
    """一趟里换过家 (difficulties #14): 金额按各家分别算再相加.

    这一条是那一族里**唯一算得出钱**的形态: 运行行那五列是累计值, 拆不出「谁产出的」,
    而逐轮记录里每一次响应都盖着服务方的名字 (模型层盖的, 见 retry/failover.py) ——
    于是记录员把用量归到各家头上, 各查各的价.

    账上那个名字仍是组合名 (「这一趟两家都用过」是事实), 而它现在有了对应的金额:
    0.0036 (主: 1000 miss x 2 + 200 out x 8, 每百万) + 0.00451 (备: 2000 miss x 1
    + 100 hit x 0.1 + 500 out x 5) = 0.00811.
    """
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    primary = _usage(miss=1000, hit=0, out=200)
    backup = _usage(miss=2000, hit=100, out=500)

    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(
            input_tokens=3100,
            output_tokens=700,
            cache_miss_tokens=3000,
            cache_hit_tokens=100,
            turns=[
                _turn(1, model="deepseek-flash", usage=primary, tokens=1200),
                _turn(2, model="gpt-6-luna", usage=backup, tokens=2600),
            ],
        ),
        model="deepseek-flash+gpt-6-luna",
    )

    [run] = database.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash+gpt-6-luna", "名字仍是事实"
    assert run["total_cost"] == Decimal("0.008110"), "金额 = 两家各算各的再相加"
    lines = run["total_cost_detail"]["lines"]
    assert [(item["model"], item["tier"], item["amount"]) for item in lines] == [
        ("deepseek-flash", "cache_miss", "0.002000"),
        ("deepseek-flash", "cache_hit", "0.000000"),
        ("deepseek-flash", "output", "0.001600"),
        ("gpt-6-luna", "cache_miss", "0.002000"),
        ("gpt-6-luna", "cache_hit", "0.000010"),
        ("gpt-6-luna", "output", "0.002500"),
    ], "明细逐行带归属 —— 读的人能按行验算"


class _Scripted:
    """按脚本答话的模型 (脚本用尽后照 `then` 抛/答) —— 端到端那条用例的两个角色."""

    def __init__(
        self, name: str, script: list[ModelResponse], *, then: Exception | None = None
    ) -> None:
        self.model = name
        self._script = list(script)
        self._then = then

    async def generate(self, *args: Any, **kwargs: Any) -> ModelResponse:
        if self._script:
            return self._script.pop(0)
        if self._then is not None:
            raise self._then
        return text_response("还答得动")

    async def aclose(self) -> None:
        """关闭连接 (替身没有连接可关)."""


async def test_a_real_run_that_switched_between_models_is_priced_per_model() -> None:
    """端到端: 真跑一遍循环 (主模型答一轮 -> 跳闸 -> 备份答完), 记录员按两家分别算钱.

    与上面那条 (手搓的轮) 的分工: 这一条跑**真的**循环与真的模型层包装, 于是它验的是
    那条缝 —— 模型层盖在响应上的服务方, 真的随 `LoopResult.turns` 走到了记录员手里.

    账: 主 (1000 miss x 2 + 100 out x 8, 每百万) 0.0028 + 备 (2000 miss x 1 +
    100 hit x 0.1 + 500 out x 5) 0.00451 = 0.00731.
    """
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    primary = _Scripted(
        "deepseek-flash",
        [
            tool_call_response(
                make_tool_call("query_order", ORDER_CALL, call_id="call_0"),
                usage=_usage(miss=1000, hit=0, out=100),
            )
        ],
        then=ModelStatusError(503, "boom"),
    )
    backup = _Scripted(
        "gpt-6-luna",
        [text_response("已发货", usage=_usage(miss=2000, hit=100, out=500))],
    )
    loop = AgentLoop(
        RetryingChatModel(
            FailoverChatModel(
                primary, backup, policy=CircuitPolicy(failure_threshold=2)
            ),
            policy=RetryPolicy(
                max_attempts=2, initial_delay=0.0, jitter=0.0, sleep=RecordingSleep()
            ),
        ),
        tools=[query_order_tool],
        saver=InMemoryCheckpointSaver(),
        thread_id=THREAD_ID,
        trace_sink=rec,
    )

    outcome = await loop.run([{"role": "user", "content": "订单到哪了"}], run_id=run_id)
    assert [turn.response.model for turn in outcome.turns] == [
        "deepseek-flash",
        "gpt-6-luna",
    ], "每一轮的响应上写着谁答的话 (拆账的依据)"

    # 会话收尾时给的就是服务台账算出来的那个组合名 (见 retry/serving.py)
    assert await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=outcome,
        model="deepseek-flash+gpt-6-luna",
    )

    [run] = database.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash+gpt-6-luna"
    assert run["total_cost"] == Decimal("0.007310"), "两家各算各的再相加"
    assert {item["model"] for item in run["total_cost_detail"]["lines"]} == {
        "deepseek-flash",
        "gpt-6-luna",
    }


async def test_a_mixed_segment_leaves_its_per_model_usage_on_the_row() -> None:
    """段内混过家的一趟: 逐模型用量**写进那一列** (下一段要拿它接着合)."""
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")

    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(
            input_tokens=3100,
            output_tokens=600,
            cache_miss_tokens=3000,
            cache_hit_tokens=100,
            turns=[
                _turn(
                    1,
                    model="deepseek-flash",
                    usage=_usage(miss=1000, hit=0, out=100),
                    tokens=1100,
                ),
                _turn(
                    2,
                    model="gpt-6-luna",
                    usage=_usage(miss=2000, hit=100, out=500),
                    tokens=2600,
                ),
            ],
        ),
        model="deepseek-flash+gpt-6-luna",
    )

    [run] = database.rows_of("charagent_runs")
    assert run["usage_by_model"] == [
        {
            "model": "deepseek-flash",
            "input_tokens": 1000,
            "cache_miss_tokens": 1000,
            "cache_hit_tokens": 0,
            "output_tokens": 100,
        },
        {
            "model": "gpt-6-luna",
            "input_tokens": 2100,
            "cache_miss_tokens": 2000,
            "cache_hit_tokens": 100,
            "output_tokens": 500,
        },
    ], "按首次出场排序, 分量一个不少"
    assert run["total_cost"] == Decimal("0.007310")


async def test_a_resumed_segment_merges_with_the_column_and_prices_both() -> None:
    """HITL 跨段 (difficulties #14 最后一块): 第二段把列上那份 + 本段那份合起来算钱.

    这是「段 1 自己也混过家」的情形 —— 只靠减法推不出来 (组合名分不了比例), 靠的
    就是段 1 收尾时留在行上的那一列. 两段合起来: 主 (段1 1000 miss + 段2 1000 miss
    + 两段各 100 out) 与备 (段1 那些), 金额 = 0.0056 + 0.00451 = 0.01011.
    """
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    segment_one = result(
        input_tokens=3100,
        output_tokens=600,
        cache_miss_tokens=3000,
        cache_hit_tokens=100,
        turns=[
            _turn(
                1,
                model="deepseek-flash",
                usage=_usage(miss=1000, hit=0, out=100),
                tokens=1100,
            ),
            _turn(
                2,
                model="gpt-6-luna",
                usage=_usage(miss=2000, hit=100, out=500),
                tokens=2600,
            ),
        ],
    )
    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=segment_one,
        model="deepseek-flash+gpt-6-luna",
    )
    assert database.rows_of("charagent_runs")[0]["usage_by_model"] is not None

    # 第二段 (人确认之后接着跑): 这一段的轮由主模型答, 而账目是**整趟累计**
    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(
            input_tokens=4100,
            output_tokens=700,
            cache_miss_tokens=4000,
            cache_hit_tokens=100,
            turns=[
                _turn(
                    3,
                    model="deepseek-flash",
                    usage=_usage(miss=1000, hit=0, out=100),
                    tokens=1100,
                )
            ],
        ),
        model="deepseek-flash",
    )

    [run] = database.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash+gpt-6-luna", "名字仍是整趟的事实"
    assert run["total_cost"] == Decimal("0.010110"), "两段合起来之后按各家分别算"
    assert run["usage_by_model"][0] == {
        "model": "deepseek-flash",
        "input_tokens": 2000,
        "cache_miss_tokens": 2000,
        "cache_hit_tokens": 0,
        "output_tokens": 200,
    }, "同名的两段相加 (段 1 的 1000 + 段 2 的 1000)"
    assert run["usage_by_model"][1]["model"] == "gpt-6-luna"


async def test_a_column_that_does_not_add_up_is_neither_priced_nor_rewritten() -> None:
    """列上那份与本段那份合不齐 (老行没有那一列): 不算钱, 也不把半份写回去."""
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    await rec.record(
        thread_id=THREAD_ID, run_id=run_id, result=result(), model="deepseek-flash"
    )

    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(
            input_tokens=4100,
            output_tokens=700,
            cache_miss_tokens=4000,
            cache_hit_tokens=100,
            turns=[
                _turn(
                    1,
                    model="gpt-6-luna",
                    usage=_usage(miss=2000, hit=100, out=500),
                    tokens=2600,
                )
            ],
        ),
        model="gpt-6-luna",
    )

    [run] = database.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash+gpt-6-luna"
    assert run["total_cost"] is None, "合不齐 -> 不拆 (拆一半比不拆更糟)"
    assert run["usage_by_model"] is None, "半份不写回去, 免得下一段在脏数据上继续合"


async def test_a_vendor_that_does_not_report_cache_miss_still_prices() -> None:
    """一家报未命中、另一家不报 (真机那两家的口径): 照样拆得开、算得出钱.

    2026-10-05 真机实测: DeepSeek 两档都给, OpenAI 兼容那家只给命中 —— 判据若去比
    未命中, 这种组合永远验不过 (而那正是这一列要服务的场景). 缺的那一档由定价那一步
    按各家自己的 `input - hit` 推出来.
    """
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    # 主: 两档都有; 备: 只有命中 (未命中要推)
    primary = Usage(
        input_tokens=1000,
        output_tokens=100,
        total_tokens=1100,
        cache_miss_tokens=1000,
        cache_hit_tokens=0,
    )
    backup = Usage(
        input_tokens=2100, output_tokens=500, total_tokens=2600, cache_hit_tokens=100
    )

    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(
            input_tokens=3100,
            output_tokens=600,
            cache_miss_tokens=1000,  # 只有主报过 -> 运行行记的是「已上报部分的和」
            cache_hit_tokens=100,
            turns=[
                _turn(1, model="deepseek-flash", usage=primary, tokens=1100),
                _turn(2, model="gpt-6-luna", usage=backup, tokens=2600),
            ],
        ),
        model="deepseek-flash+gpt-6-luna",
    )

    [run] = database.rows_of("charagent_runs")
    assert run["total_cost"] is not None, "口径不同不该挡住拆账"
    lines = {
        item["tier"]: item
        for item in run["total_cost_detail"]["lines"]
        if item["model"] == "gpt-6-luna"
    }
    assert lines["cache_miss"]["derived"] is True
    assert lines["cache_miss"]["tokens"] == 2000, "2100 - 100, 按它自己的输入总量推"


async def test_a_partial_split_is_not_priced() -> None:
    """逐轮那几份只覆盖了一部分 (HITL 续跑的第二段): 整趟不拆, 按组合名报「算不出来」.

    为什么宁可算不出来: 第二段的计数器从快照接着数, 而它的轮里没有上一段的用量 ——
    硬拆出来的账会漏掉前一段, 那比没有金额更糟 (它看起来像个能对账的数).
    """
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(
            input_tokens=1000,
            output_tokens=200,
            cache_miss_tokens=1000,
            cache_hit_tokens=0,
        ),
        model="deepseek-flash",
    )
    [before] = database.rows_of("charagent_runs")
    assert before["total_cost"] is not None

    # 第二段: 累计用量 (3100/700/3000/100) 大于这一段轮里那一家那一点
    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(
            input_tokens=3100,
            output_tokens=700,
            cache_miss_tokens=3000,
            cache_hit_tokens=100,
            turns=[
                _turn(
                    1,
                    model="gpt-6-luna",
                    usage=_usage(miss=2000, hit=100, out=500),
                    tokens=2600,
                )
            ],
        ),
        model="gpt-6-luna",
    )

    [run] = database.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash+gpt-6-luna"
    assert run["total_cost"] is None, "拆一部分比不拆更糟"
    assert run["total_cost_detail"]["reason"] == CostGap.NO_PRICE.value


async def test_two_segments_on_the_same_model_keep_one_name() -> None:
    """两段落在同一家: 名字不该因为「写了两遍」而叠起来, 金额照算."""
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")
    usage = {"input_tokens": 100, "cache_miss_tokens": 100, "output_tokens": 10}

    for _ in range(2):
        await rec.record(
            thread_id=THREAD_ID,
            run_id=run_id,
            result=result(**usage),
            model="deepseek-flash",
        )

    [run] = database.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash"
    assert run["total_cost"] is not None


async def test_a_name_already_joined_by_the_session_is_not_joined_again() -> None:
    """会话那边的台账可能已经拼过一次 (`主+备`): 再合一次不该叠字.

    两条路都会拼: 一趟之内切过家 -> 服务台账拼 (retry/serving.py); 一趟分两段 ->
    这里拼. 同一次收尾可能两条都碰上, 所以合并必须**按分隔符拆开再合** —— 否则
    记出来的是 `主+备+备` 这种谁也读不懂的名字.
    """
    database = FakeRecordDatabase()
    rec = _priced_recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="订单到哪了")

    await rec.record(
        thread_id=THREAD_ID, run_id=run_id, result=result(), model="deepseek-flash"
    )
    await rec.record(
        thread_id=THREAD_ID,
        run_id=run_id,
        result=result(),
        model="deepseek-flash+gpt-6-luna",
    )

    [run] = database.rows_of("charagent_runs")
    assert run["model"] == "deepseek-flash+gpt-6-luna"


async def test_a_failed_segment_without_a_question_writes_only_the_notice() -> None:
    """续跑段失败 (`question=None`): 只写「这一轮没答完」, 不编一句用户提问.

    编一句出来最省事, 但那条 `user` 行会让记录撒谎 —— 「只有真由用户输入产生的
    消息才是 user」是这一层立着的一条硬规矩 (见 db/conversation.py).
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="")

    written = await rec.record_unfinished(
        thread_id=THREAD_ID,
        question=None,
        status=RunStatus.FAILED,
        run_id=run_id,
    )

    assert written is True
    messages = database.rows_of("charagent_messages")
    assert [(row["role"], row["content"]) for row in messages] == [
        ("system", UNFINISHED_TURN_TEXT)
    ]
    [run] = database.rows_of("charagent_runs")
    assert run["status"] == RunStatus.FAILED.value


async def test_a_suspension_writes_what_to_ask_as_the_run_waits() -> None:
    """挂起那一段的收尾 (issue 34): 运行行落成 `waiting_user`, 而那条调用带上要问什么.

    两处一起看, 因为它们是同一件事的两个落点:
    - 运行行: `waiting_user` 且 **没有结束时刻** —— 那一次运行还开着, 人给了结论
      才接着跑 (状态由 `RUN_STATUS_FOR_OUTCOME` 从 SUSPENDED 翻过来)
    - 工具调用行: `needs_approval` + 话术 + 缺失项 —— 刷新页面之后前端靠它重建那张
      确认卡 (没有话术就是一张没有字的卡)
    """
    database = FakeRecordDatabase()
    rec = recorder(database)
    run_id = await rec.begin(thread_id=THREAD_ID, title="帮我付了这一单")
    messages = [TWO_LINES[0], ASSISTANT_CALL]
    suspended = result(
        content=None,
        outcome=LoopOutcome.SUSPENDED,
        messages=messages,
        turns=[
            TurnRecord(
                turn=1,
                response=ModelResponse(
                    content=None, finish_reason=FinishReason.TOOL_CALLS
                ),
                messages=list(messages),
                tokens=0,
                elapsed_ms=0.0,
                calls=[
                    fact(
                        outcome=ToolCallOutcome.NEEDS_APPROVAL,
                        name="pay_order",
                        approval_prompt="要输支付密码",
                        approval_needs=("payment_password",),
                    )
                ],
            )
        ],
    )

    await rec.record(thread_id=THREAD_ID, result=suspended, run_id=run_id, since=1)

    [run] = database.rows_of("charagent_runs")
    assert run["status"] == RunStatus.WAITING_USER.value, "挂起不是终态"
    assert run["finished_at"] is None, "那一次运行还开着"
    [row] = database.rows_of("charagent_tool_calls")
    assert row["status"] == "needs_approval"
    assert row["approval_prompt"] == "要输支付密码"
    assert row["approval_needs"] == ["payment_password"]
    assert row["approved_at"] is None
    assert row["approved_by"] is None
