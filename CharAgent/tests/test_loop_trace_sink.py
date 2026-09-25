"""落库协作者 (`TraceSink`) 这条线: loop 在运行中途交出去的是什么 (ticket 27).

被测的是**交付的形状** —— 交了几次、每次交的是哪几条消息与哪几条调用、下标对不对.
落库那一侧 (写进哪张表 / 幂等 / 收尾补齐 / 修订) 归 `test_db_recorder.py` 用假库守.

**为什么下标要逐次钉住**: 记录层拿它算 `message_id` (`run_id:下标`). 算错不会报错,
只会把工具调用行挂到别人的消息上 —— 那是静默的错数据 (查得到行, 但归属是错的).

两拍的语义 (与 `runs` 的 begin/finish 同构):

| 拍 | 什么时候 | 交什么 |
|---|---|---|
| 执行前 | `_handle_tool_turn` 里, 工具还没跑 | 那条 assistant 隐藏行 |
| | | + 几条调用的 `pending` 事实 |
| 每轮收尾 | `_record_turn` 里 | 这一轮新产生的其余消息 |
| | | + 有结论的那几条调用 |

一条消息**只交一次** (下标推进只有一个口径, 见 `LoopState.flushed`).
"""

from __future__ import annotations

from typing import Any

from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.agent import AgentLoop, ToolCallOutcome
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.hooks import HookRegistry
from CharAgent.hooks.utils.types import Decision, HookPoint
from CharAgent.model.utils.types import ModelMessage
from CharAgent.tool import tool

USER_MSG: ModelMessage = {"role": "user", "content": "订单到哪了"}
ORDER_CALL = '{"order_no": "SF123"}'


@tool
def query_order(
    order_no: str,
) -> str:
    """查订单状态.

    Args:
        order_no: 订单号.
    """
    return "已发货"


class RecordingSink:
    """把每次交付原样记下来的落库协作者 (只实现协议, 一个字都不写库).

    attributes:
        batches: 每次交付一项 (`thread_id` / `run_id` / `start` / `messages` / `calls`).
    """

    def __init__(self) -> None:
        self.batches: list[dict[str, Any]] = []

    async def flush(
        self,
        *,
        thread_id: str,
        run_id: str,
        start: int,
        messages: Any,
        calls: Any = (),
    ) -> None:
        """收下这一次交付 (TraceSink 协议)."""
        self.batches.append(
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "start": start,
                "messages": list(messages),
                "calls": list(calls),
            }
        )


def script() -> MockLLM:
    """「先查订单, 再照结果作答」这一段脚本 (一次工具轮 + 一次答复轮)."""
    return MockLLM.scripted(
        [
            tool_call_response(make_tool_call("query_order", ORDER_CALL)),
            text_response("已发货"),
        ]
    )


def build(
    model: Any, sink: RecordingSink, *, thread_id: str, **kwargs: Any
) -> AgentLoop:
    """按生产里的形状装配 loop: 快照存储与会话编号成对给.

    为什么要 saver: 落库要**会话编号**做分区键 (消息行按它归属), 而框架里 thread_id
    与 saver 是成对给的 (`ChatSession` 两者必有) —— 于是「有中途落库」这件事在装配
    上天然跟着两者一起出现.
    """
    return AgentLoop(
        model,
        saver=InMemoryCheckpointSaver(),
        thread_id=thread_id,
        trace_sink=sink,
        **kwargs,
    )


async def test_a_tool_turn_is_handed_over_twice_with_its_facts() -> None:
    """一次带工具的运行: 三次交付 (执行前 / 工具轮收尾 / 答复轮收尾), 下标连着走.

    三次而不是两次: 答复那一轮自己也产生一条消息 (最终答复), 它属于那一轮的收尾拍.
    """
    sink = RecordingSink()
    loop = build(script(), sink, thread_id="trace-1", tools=[query_order])

    await loop.run([dict(USER_MSG)], run_id="run-1")

    assert [
        (batch["start"], [m["role"] for m in batch["messages"]])
        for batch in sink.batches
    ] == [
        (1, ["assistant"]),  # 执行前: 那条「我要去查一下」
        (2, ["tool"]),  # 工具轮收尾: 回填
        (3, ["assistant"]),  # 答复轮收尾: 最终答复
    ], "一条消息只交一次, 下标连着走"
    assert {batch["thread_id"] for batch in sink.batches} == {"trace-1"}
    assert {batch["run_id"] for batch in sink.batches} == {"run-1"}
    assert sink.batches[0]["messages"][0]["tool_calls"], (
        "执行前交的那条就是发起调用的 assistant 消息 (工具调用行按它算归属)"
    )


async def test_the_execution_before_and_after_facts_carry_the_whole_story() -> None:
    """两拍的事实: 执行前是 `pending` 且没有结论, 执行后带着结果与耗时.

    参数是**原样 JSON 字符串** (不预解析: 畸形 JSON 正是自纠错路径的信号);
    `message_index` 两拍指向同一条 assistant 消息 —— 于是两次写落在同一行上.
    """
    sink = RecordingSink()
    loop = build(script(), sink, thread_id="trace-2", tools=[query_order])

    await loop.run([dict(USER_MSG)], run_id="run-2")

    [before] = sink.batches[0]["calls"]
    [wire_call] = sink.batches[0]["messages"][0]["tool_calls"]
    assert before.tool_call_id == wire_call["id"], "编号与 wire 里那一条对得上"
    assert before.tool_name == "query_order"
    assert before.arguments == ORDER_CALL, "原样 JSON, 一个字符都不动"
    assert before.message_index == 1, "发起它的那条 assistant 消息在历史里的下标"
    assert (before.outcome, before.result, before.duration_ms) == (
        ToolCallOutcome.PENDING,
        None,
        None,
    ), "还没执行: 没有结论, 也没有耗时 (NULL 与 0 毫秒不是一回事)"

    [after] = sink.batches[1]["calls"]
    assert (after.outcome, after.result) == (ToolCallOutcome.SUCCEEDED, "已发货")
    assert after.duration_ms is not None and after.duration_ms >= 0
    assert after.message_index == before.message_index, "两次写的是同一行"


async def test_a_denied_call_is_handed_over_as_a_failure_with_the_reason() -> None:
    """被护栏**拒绝**的调用同样是一条事实: `failed`, 原因是那句话.

    它不是「没有发生的事」—— 它是「模型想做什么」的证据 (ticket 27 的验收之一),
    漏掉它, 轨迹上就少了一次模型真实的意图.
    """
    registry = HookRegistry()
    registry.register(
        HookPoint.BEFORE_TOOL_EXECUTE,
        lambda **kwargs: Decision.reject("这一单要先在页面上确认"),
    )
    sink = RecordingSink()
    loop = build(
        MockLLM.scripted(
            [
                tool_call_response(make_tool_call("query_order", ORDER_CALL)),
                text_response("那我先不查了"),
            ]
        ),
        sink,
        thread_id="trace-3",
        tools=[query_order],
        hooks=registry,
    )

    await loop.run([dict(USER_MSG)], run_id="run-3")

    [fact] = sink.batches[1]["calls"]
    assert fact.outcome is ToolCallOutcome.FAILED
    assert fact.result == "这一单要先在页面上确认", "拒绝原因就是这一条的结果"


async def test_nothing_is_handed_over_without_a_run_id() -> None:
    """没开账 (run_id 为 None) 时一次都不交 —— 没有可归属的行.

    它对应「没配记录层」那种装配: 会话压根没 `begin`, 于是 loop 不必替它写.
    """
    sink = RecordingSink()
    loop = build(script(), sink, thread_id="trace-4", tools=[query_order])

    await loop.run([dict(USER_MSG)])

    assert sink.batches == []


async def test_a_run_without_tools_still_hands_over_its_answer() -> None:
    """纯聊天那一轮也要交 (记录层的「提问行当场落库」是同一条路).

    没有工具时只有一次交付 (答复轮收尾), 且没有调用事实 —— 空列表不是漏交,
    是这一次真的没调工具.
    """
    sink = RecordingSink()
    loop = build(MockLLM.fixed(text_response("答好了")), sink, thread_id="trace-5")

    await loop.run([dict(USER_MSG)], run_id="run-5")

    [batch] = sink.batches
    assert (batch["start"], [m["content"] for m in batch["messages"]]) == (
        1,
        ["答好了"],
    )
    assert batch["calls"] == []
