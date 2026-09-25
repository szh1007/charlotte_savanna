"""stream 包单元测试 (#4): 事件类型 / seq 编号 / 状态机不变量.

场景 → 断言:
- EventType 七类齐全 (事件名与 API 契约一一对应)
- seq: 每 run 从 1 起单调递增 (断点续拉的事件 id)
- 状态机不变量 (违反即 EventSequenceError, 语义见 bus.EventBus docstring):
  1) tool_result 必须匹配一条未闭合 tool_call (按 tool_call_id 配对)
  2) 重复的 tool_call_id (同一 id 开两次)
  3) 仍有未闭合 tool_call 时不得发终局事件 (工具结果必须回填完)
  4) 终局事件之后不得再发任何事件 (含第二个终局)
- reasoning 旁路通道: 不改变主状态, 任意非终局位置可发
- 分发: sink (sync / async) 先收到事件, on_event hook 随后收到 (顺序即契约)
- to_dict: {"type", "seq", **data} —— SSE 直通形状, run_id 由 server 层注入

被测对象是纯状态机 (不接 loop); loop 接线集成见 test_loop_events.py.

大白话版 (这份「验货单」在验什么):
- 喊话规范对不对: 7 种句式齐全.
- 场记的编号对不对: 每句话从 1 开始往上涨.
- 纪律委员真的会拦人吗: 没说「要去查」就喊「查回来了」要报错; 同一句话没
  收到回音就重喊要报错; 工具还没回结果就喊「答完了」要报错; 喊完「答完了」
  再喊别的要报错; 而跨轮复用同一个工具 id (上游逐轮重置 id) 属正常, 放行.
- 大喇叭的顺序对不对: 先给门外观众 (sink), 再给台下观察员 (on_event hook).
"""

from __future__ import annotations

import asyncio

import pytest

from CharAgent.hooks import HookPoint, HookRegistry
from CharAgent.stream import EventBus, EventSequenceError, EventType, StreamEvent


async def _open_and_close(bus: EventBus, call_id: str = "call_1") -> None:
    """开一条 tool_call 并把结果回填 (合法两步, 供多处复用)."""
    await bus.emit(
        EventType.TOOL_CALL,
        tool_call_id=call_id,
        tool_name="query_order",
        arguments='{"order_no": "20260701123456"}',
        status="started",
    )
    await bus.emit(
        EventType.TOOL_RESULT,
        tool_call_id=call_id,
        tool_name="query_order",
        status="ok",
        summary="已发货",
        duration_ms=12.0,
    )


# ---------------------------------------------------------------------------
# 事件类型与 seq
# ---------------------------------------------------------------------------


def test_event_types_cover_contract() -> None:
    """八类事件与 API 契约的事件名一致 (approval_required 是 issue 34 落的那一个)."""
    assert [t.value for t in EventType] == [
        "thinking",
        "tool_call",
        "tool_result",
        "reasoning",
        "context_compacted",
        "approval_required",
        "final",
        "error",
    ]


async def test_seq_starts_at_one_and_increments() -> None:
    """seq 每 run 从 1 起单调递增 (断点续拉的 after_event_id)."""
    bus = EventBus()
    first = await bus.emit(EventType.THINKING, message="正在理解问题")
    await _open_and_close(bus)
    final = await bus.emit(
        EventType.FINAL, content="已受理", finish_reason="stop", outcome="finished"
    )

    assert (first.seq, final.seq) == (1, 4)
    assert bus.seq == 4  # 已产出事件数


async def test_to_dict_shape_is_sse_ready() -> None:
    """to_dict 为 SSE data 直通形状: type + seq + 业务字段平铺.

    run_id 不在框架层 (无 run 概念), 由 P1 server 转发时注入.
    """
    bus = EventBus()
    event = await bus.emit(
        EventType.TOOL_CALL,
        tool_call_id="call_1",
        tool_name="query_order",
        arguments="{}",
        status="started",
    )

    assert event.to_dict() == {
        "type": "tool_call",
        "seq": 1,
        "tool_call_id": "call_1",
        "tool_name": "query_order",
        "arguments": "{}",
        "status": "started",
    }


# ---------------------------------------------------------------------------
# 状态机不变量
# ---------------------------------------------------------------------------


async def test_tool_result_without_open_call_rejected() -> None:
    """不变量 1: tool_result 找不到匹配的未闭合 tool_call → 报错 (配对完整性)."""
    bus = EventBus()
    with pytest.raises(EventSequenceError, match="未闭合"):
        await bus.emit(EventType.TOOL_RESULT, tool_call_id="call_x", status="ok")


async def test_tool_result_missing_id_rejected() -> None:
    """不变量 1 前置: tool_result 未带 tool_call_id → 报错 (无法配对)."""
    bus = EventBus()
    with pytest.raises(EventSequenceError, match="tool_call_id"):
        await bus.emit(EventType.TOOL_RESULT, status="ok")


async def test_duplicate_tool_call_id_rejected() -> None:
    """不变量 2: 同一 tool_call_id 开两次 → 报错 (模型侧 id 必须唯一)."""
    bus = EventBus()
    await bus.emit(EventType.TOOL_CALL, tool_call_id="call_1", tool_name="a")
    with pytest.raises(EventSequenceError, match="重复"):
        await bus.emit(EventType.TOOL_CALL, tool_call_id="call_1", tool_name="a")


async def test_same_call_id_allowed_after_close() -> None:
    """同一 tool_call_id 闭合后可再次开启 (只约束未闭合期间).

    真实上游的 tool_call_id 逐响应重置 (如 `call_0` 每轮重来), 跨轮复用同一 id
    属正常现象; 不变量② 只管「同一批次内不得重复开启」.
    """
    bus = EventBus()
    await _open_and_close(bus, call_id="call_0")
    await _open_and_close(bus, call_id="call_0")  # 第二轮复用同一 id: 合法

    assert bus.seq == 4


async def test_terminal_rejected_while_call_open() -> None:
    """不变量 3: 工具结果没回填完就发终局事件 → 报错 (历史不得半截)."""
    bus = EventBus()
    await bus.emit(EventType.TOOL_CALL, tool_call_id="call_1", tool_name="a")
    with pytest.raises(EventSequenceError, match="未回填"):
        await bus.emit(EventType.FINAL, content="答案")


async def test_no_event_after_terminal() -> None:
    """不变量 4: 终局事件之后不得再发事件 (含第二个终局)."""
    bus = EventBus()
    await bus.emit(EventType.FINAL, content="答案")
    with pytest.raises(EventSequenceError, match="终局"):
        await bus.emit(EventType.THINKING, message="再想想")
    with pytest.raises(EventSequenceError, match="终局"):
        await bus.emit(EventType.ERROR, error={"code": "x", "message": "y"})


async def test_parallel_calls_all_closed_before_final() -> None:
    """并行语义: 同一轮多条 tool_call 全部闭合后终局才合法 (#1)."""
    bus = EventBus()
    await bus.emit(EventType.TOOL_CALL, tool_call_id="call_1", tool_name="a")
    await bus.emit(EventType.TOOL_CALL, tool_call_id="call_2", tool_name="b")
    await bus.emit(EventType.TOOL_RESULT, tool_call_id="call_1", status="ok")
    with pytest.raises(EventSequenceError, match="未回填"):
        await bus.emit(EventType.FINAL, content="答案")
    await bus.emit(EventType.TOOL_RESULT, tool_call_id="call_2", status="error")
    assert (await bus.emit(EventType.FINAL, content="答案")).seq == 5


async def test_full_legal_sequence_accepted() -> None:
    """合法序列: thinking → tool_call → tool_result → final 全程无异常."""
    bus = EventBus()
    await bus.emit(EventType.THINKING, message="让我先查一下订单")
    await _open_and_close(bus)
    await bus.emit(EventType.FINAL, content="订单已发货")

    assert bus.seq == 4


async def test_reasoning_is_side_channel() -> None:
    """reasoning 旁路: 任何非终局位置可发, 且不改变主状态 (不算未闭合工具)."""
    bus = EventBus()
    await bus.emit(EventType.REASONING, delta="正在核对订单号")
    await bus.emit(EventType.TOOL_CALL, tool_call_id="call_1", tool_name="a")
    await bus.emit(EventType.REASONING, delta="工具返回了, 继续推理")
    await bus.emit(EventType.TOOL_RESULT, tool_call_id="call_1", status="ok")
    await bus.emit(EventType.REASONING, delta="可以作答了")
    assert (await bus.emit(EventType.FINAL, content="答案")).seq == 6


async def test_context_compacted_is_side_channel() -> None:
    """上下文压缩 (#7) 同样是旁路: 不参与工具配对, 也不会因「压过」被拦住."""
    bus = EventBus()
    await bus.emit(EventType.CONTEXT_COMPACTED, turn=1, dropped=3)
    await _open_and_close(bus)
    await bus.emit(EventType.CONTEXT_COMPACTED, turn=2, dropped=2)

    assert (await bus.emit(EventType.FINAL, content="答案")).seq == 5


async def test_context_compacted_rejected_after_terminal() -> None:
    """压缩事件也受不变量 4 管: 终局之后不得再发任何事件."""
    bus = EventBus()
    await bus.emit(EventType.FINAL, content="答案")

    with pytest.raises(EventSequenceError, match="终局"):
        await bus.emit(EventType.CONTEXT_COMPACTED, turn=1, dropped=3)


# ---------------------------------------------------------------------------
# 分发: sink 与 on_event hook
# ---------------------------------------------------------------------------


async def test_sync_sink_receives_events() -> None:
    """同步 sink (CLI / 测试收集器) 按产出顺序收到事件."""
    received: list[StreamEvent] = []
    bus = EventBus(sink=received.append)
    await _open_and_close(bus)

    assert [e.type for e in received] == [EventType.TOOL_CALL, EventType.TOOL_RESULT]


async def test_async_sink_receives_events() -> None:
    """异步 sink (P1 server 推队列) 同样被 await 到位."""
    received: list[StreamEvent] = []

    async def sink(event: StreamEvent) -> None:
        await asyncio.sleep(0)
        received.append(event)

    bus = EventBus(sink=sink)
    await bus.emit(EventType.FINAL, content="答案")

    assert [e.type for e in received] == [EventType.FINAL]


async def test_sink_precedes_on_event_hook() -> None:
    """顺序契约: sink (传输通道) 先于 on_event hook (扩展点) 收到事件."""
    order: list[str] = []

    async def sink(event: StreamEvent) -> None:
        order.append(f"sink:{event.type.value}")

    def hook(*, event: StreamEvent) -> None:
        order.append(f"hook:{event.type.value}")

    hooks = HookRegistry()
    hooks.register(HookPoint.ON_EVENT, hook)
    bus = EventBus(sink=sink, hooks=hooks)
    await bus.emit(EventType.THINKING, message="思考中")

    assert order == ["sink:thinking", "hook:thinking"]


async def test_bus_without_sink_or_hooks_is_noop() -> None:
    """空注册 (无 sink / 无 hook): emit 只做状态机校验, 不产生任何分发副作用."""
    bus = EventBus()
    event = await bus.emit(EventType.THINKING, message="思考中")

    assert event.data == {"message": "思考中"}  # 事件对象仍返回给调用方
    assert (event.seq, bus.seq) == (1, 1)
