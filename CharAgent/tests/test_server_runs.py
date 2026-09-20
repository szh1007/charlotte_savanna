"""server 的流与在册: 序号记账 / 终局事件恰好一个 / 失败与取消的收尾.

这一页盯着三件容易做错的事 (文档在 runs.py):

1. **终局事件恰好一个**: loop 正常结束时自己发 final, 本层不再补; 取消与「模型
   直接失败」时 loop 不发, 由本层补一个 —— 补之前先看流里有没有, 不许出现两个.
2. **补的那个接着编号**: seq 是客户端的连续性依据, 中间断一号等于「有事件丢了」.
3. **取消 / 失败都要有收尾**: 一次运行不论怎么结束, 事件流都得有个明确的结尾
   (否则前端只能靠超时猜), 而且收尾哨兵一定要发出去 (生成器靠它收线).

顺带钉住日志: 失败在进程里留一笔带 traceback 的记录 —— 这是框架里唯一打日志的
地方 (其余部分失败一律上抛给调用方), 所以它值得一条用例看着.
"""

from __future__ import annotations

import asyncio

import pytest

from CharAgent.server.runs import (
    CANCELLED_TEXT,
    RunHandle,
    RunRegistry,
    RunStream,
    close_stream,
    new_run_id,
)
from CharAgent.server.utils.types import CANCELLED_CODE, RUN_FAILED_CODE
from CharAgent.stream import EventType, StreamEvent


def take(stream: RunStream) -> list[StreamEvent | None]:
    """把流里已排队的东西**原样**取出来 (None 就是收尾哨兵, 位置保留).

    一次取完而不是分几次取: 队列是先进先出的, 取两遍会把哨兵先吃掉.
    保留 None 的位置才能断言「哨兵排在所有事件之后」.
    """
    items: list[StreamEvent | None] = []
    while not stream.queue.empty():
        items.append(stream.queue.get_nowait())
    return items


def events_of(items: list[StreamEvent | None]) -> list[StreamEvent]:
    """取出的原样序列 → 只看事件 (哨兵滤掉)."""
    return [item for item in items if item is not None]


# ---------------------------------------------------------------------------
# 流: 记账与补终局事件
# ---------------------------------------------------------------------------


def test_pushing_events_keeps_the_sequence() -> None:
    """入流的事件逐个记账: 最后一个 seq 与「是否已终局」是补发时的判据."""
    stream = RunStream("run-1")

    stream.push(StreamEvent(type=EventType.THINKING, seq=1, data={"message": "在想"}))
    assert (stream.last_seq, stream.closed) == (1, False)

    stream.push(StreamEvent(type=EventType.FINAL, seq=2, data={"content": "答完了"}))
    assert (stream.last_seq, stream.closed) == (2, True)


def test_a_missing_terminal_event_is_added_after_the_last_one() -> None:
    """补的终局事件接着编号 —— 中间留一号, 客户端会以为丢了事件."""
    stream = RunStream("run-1")
    stream.push(StreamEvent(type=EventType.THINKING, seq=1, data={"message": "在想"}))
    stream.push(
        StreamEvent(type=EventType.TOOL_CALL, seq=3, data={"tool_call_id": "c1"})
    )
    take(stream)  # 先把前面的事件取走, 下面只看补的那一个

    assert stream.emit_terminal(CANCELLED_CODE, CANCELLED_TEXT) is True

    items = take(stream)
    [terminal] = events_of(items)
    assert terminal.seq == 4, "接着最后一个事件编号 (前面最后一个的 seq 是 3)"
    assert terminal.type is EventType.ERROR
    assert terminal.data["error"] == {"code": CANCELLED_CODE, "message": CANCELLED_TEXT}


def test_a_terminal_event_is_never_added_twice() -> None:
    """已经终局就不再补 —— 终局事件恰好一个 (事件流第四条不变量)."""
    stream = RunStream("run-1")
    stream.push(StreamEvent(type=EventType.FINAL, seq=1, data={"content": "答完了"}))

    assert stream.emit_terminal(RUN_FAILED_CODE, "不该出现") is False

    items = take(stream)
    assert [event.type for event in events_of(items)] == [EventType.FINAL]


def test_finish_marks_the_end_of_production() -> None:
    """收尾哨兵是「生产结束」的信号 (生成器靠它收线, 不必靠猜)."""
    stream = RunStream("run-1")

    stream.finish()

    items = take(stream)
    assert events_of(items) == [], "哨兵不是事件"
    assert stream.last_seq == 0, "哨兵不是事件, 不该动序号"


def test_run_ids_are_unique() -> None:
    """运行编号两两不同 (取消靠它指名道姓, 撞号就是取消错了人)."""
    assert len({new_run_id() for _ in range(100)}) == 100


# ---------------------------------------------------------------------------
# 收尾: 怎么结束 → 流里补什么 (close_stream)
# ---------------------------------------------------------------------------


async def quiet() -> None:
    """一个正常跑完的任务."""


async def boom() -> None:
    """一个抛异常的任务."""
    raise RuntimeError("上游 500")


async def hanging() -> None:
    """一个跑不完的任务 (等一个永远不来的事件)."""
    await asyncio.Event().wait()


async def test_a_normal_run_needs_no_extra_terminal_event() -> None:
    """跑通了就不补: 终局事件由 loop 自己发 (本层是多事的那一方)."""
    stream = RunStream("run-1")
    task = asyncio.create_task(quiet())
    await task

    close_stream(task, stream)

    assert take(stream) == [None], "正常跑完只该有一个收尾哨兵"


async def test_a_failing_run_ends_with_a_factual_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """跑挂了要有明确的结尾: 补一个 error, 顺带在进程日志里留一笔.

    这条兑现的是「业务回调抛配置类错误 → 客户端收到明确的错误响应」在**运行中**
    的那一半: 流已经开出去了, 不可能再回一个状态码, 只能靠终局事件收尾.
    """
    caplog.set_level("ERROR", logger="charagent.server")
    stream = RunStream("run-1")
    stream.push(StreamEvent(type=EventType.THINKING, seq=1, data={"message": "在想"}))
    task = asyncio.create_task(boom())
    await asyncio.gather(task, return_exceptions=True)

    close_stream(task, stream)

    items = take(stream)
    events = events_of(items)
    assert [event.type for event in events] == [EventType.THINKING, EventType.ERROR], (
        "先有跑出来的事件, 后有补的终局事件"
    )
    assert items[-1] is None, "失败也要收尾, 不然客户端一直等"
    terminal = events[-1]
    assert terminal.seq == 2, "接着最后一个事件编号"
    assert terminal.data["error"]["code"] == RUN_FAILED_CODE
    message = terminal.data["error"]["message"]
    assert "RuntimeError" in message and "上游 500" in message, "消息是事实"
    assert any(record.exc_info for record in caplog.records), (
        "失败要带 traceback 记一笔"
    )


class BrokenStrError(Exception):
    """一个连 __str__ 都坏掉的异常 (读没初始化的字段就是这种)."""

    def __str__(self) -> str:
        raise RuntimeError("str 也坏了")


async def test_a_failure_whose_exception_is_broken_still_reports() -> None:
    """异常对象自己坏了, 也得把「这次运行失败了」说出去 (退化成只剩类名).

    这不是刁难: 自定义异常里读到没初始化的字段就会这样, 而那时的危险是**连锁**的
    —— 说明拼不出来 → 终局事件发不出去 → 客户端一直等. 所以描述异常这件事自己
    要兜住自己.
    """

    async def broken() -> None:
        raise BrokenStrError

    stream = RunStream("run-1")
    task = asyncio.create_task(broken())
    await asyncio.gather(task, return_exceptions=True)

    close_stream(task, stream)

    items = take(stream)
    [terminal] = events_of(items)
    assert terminal.data["error"] == {
        "code": RUN_FAILED_CODE,
        "message": "BrokenStrError",
    }, "退化成类名, 但事件照发"
    assert items[-1] is None


async def test_a_cancelled_run_is_reported_as_cancelled() -> None:
    """取消要有明确的结尾 (error + code=cancelled).

    框架在取消时**不产终局事件** (agent/loop.py 写明「由 server 层负责」), 所以
    这一条是本层的责任: 前端收到它才会收尾, 而不是一直转圈.
    """
    stream = RunStream("run-1")
    task = asyncio.create_task(hanging())
    await asyncio.sleep(0)  # 让它跑起来
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    close_stream(task, stream)

    items = take(stream)
    [terminal] = events_of(items)
    assert items[-1] is None
    assert terminal.data["error"] == {
        "code": CANCELLED_CODE,
        "message": CANCELLED_TEXT,
    }


async def test_a_task_cancelled_before_it_ever_ran_is_still_closed() -> None:
    """任务一步都没跑就被取消, 事件流照样要有结尾.

    这是**收尾挂在任务回调上**而不是协程 finally 里的理由: 协程体没执行过,
    finally 就不会跑 —— 事件流会永远等一个不来的结尾 (客户端挂死). 真实触发
    路径很短: 请求刚建好任务, 客户端转身就走.
    """
    ran: list[bool] = []

    async def never_ran() -> None:
        ran.append(True)

    stream = RunStream("run-1")
    task = asyncio.create_task(never_ran())
    task.cancel()  # 一个事件循环 tick 都不给它
    await asyncio.sleep(0)

    assert task.cancelled()
    assert ran == [], "这个协程体确实一行都没跑"

    close_stream(task, stream)

    items = take(stream)
    [terminal] = events_of(items)
    assert terminal.data["error"]["code"] == CANCELLED_CODE
    assert items[-1] is None


async def test_a_failure_after_the_terminal_event_adds_nothing() -> None:
    """已经终局之后才炸掉的收尾, 不再补第二个终局事件."""
    stream = RunStream("run-1")

    async def late_boom() -> None:
        stream.push(
            StreamEvent(type=EventType.FINAL, seq=1, data={"content": "答完了"})
        )
        raise RuntimeError("收尾时炸了")

    task = asyncio.create_task(late_boom())
    await asyncio.gather(task, return_exceptions=True)

    close_stream(task, stream)

    items = take(stream)
    assert [event.type for event in events_of(items)] == [EventType.FINAL]
    assert items[-1] is None


# ---------------------------------------------------------------------------
# 在册
# ---------------------------------------------------------------------------


async def test_a_run_is_registered_while_it_runs_and_dropped_when_it_ends() -> None:
    """在跑的运行查得到句柄, 跑完就出册 (「在册」与「在跑」同真同假).

    句柄是取消唯一的抓手 (07 的端点手上只有一个 run_id), 所以「登记」这件事本身
    值得断言 —— 它不登记就再也够不着那次运行了.
    """
    registry = RunRegistry()
    task = asyncio.create_task(quiet())

    registry.register(RunHandle(run_id="run-1", thread_id="toy:chat-1", task=task))
    assert registry.run_ids == ("run-1",)
    handle = registry.get("run-1")
    assert handle is not None and handle.task is task
    assert handle.thread_id == "toy:chat-1", "出错时要一眼看出是哪段会话"

    await task
    registry.finish("run-1")
    registry.finish("run-1")  # 幂等: 收尾路径可能不止一条

    assert registry.get("run-1") is None
    assert registry.run_ids == ()
