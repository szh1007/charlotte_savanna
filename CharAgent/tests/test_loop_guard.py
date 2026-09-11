"""循环防护测试 (issue 04 / difficulties #3): 三种软限制 + kill switch.

场景 → 断言:
- max_turns: 模型永远要调工具时轮数用尽即停 (不无限循环); 已发出的工具
  调用执行完并回填, 消息历史保持合法 (软限制「这一轮结束后才判断」)
- token 预算: 累计 usage 超限后不再发起下一轮模型调用
- wall-clock: 单轮模型响应超时时长预算后停止 (睡眠注入, 确定性)
- kill switch: asyncio.Task.cancel 在工具执行中即时打断, CancelledError
  快速传播 (区别于软限制的「轮后判断」—— 即时性)
- 软限制触发后结果里 outcome 指明触发点, 调用方可区分处置

工具就地定义 (Seam 3); 模型为 ScriptedModel (Seam 1).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import pytest
from mock_llm import (
    ScriptedModel,
    make_tool_call,
    text_response,
    tool_call_response,
)

from CharAgent.agent import AgentLoop, GuardConfigError, LoopGuard, LoopOutcome
from CharAgent.model.utils.types import FinishReason, ModelMessage, ModelResponse, Usage
from CharAgent.tool import Tool, tool

USER_MSG = {"role": "user", "content": "反复查询直到完成"}


class _FakeClock:
    """固定时钟: 值由测试手动推进, 经 LoopGuard._time_source 注入缝使用.

    wall-clock 测试由此完全确定化 (真实时间 + 短预算只剩 10ms 余量,
    慢 CI 上有抖动风险; #61 注入随机/时间源的原则).
    """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _echo(
    message: str,
) -> str:
    """回声载体 (无限循环防护测试的调用对象)."""
    return f"echo:{message}"


def _make_slow_tool(started: asyncio.Event) -> Tool:
    """30s 慢工具 (kill switch 打断目标): started 由闭包捕获, 不暴露给模型."""

    @tool(name="slow")
    async def slow() -> str:
        started.set()
        await asyncio.sleep(30)
        return "done"

    return slow


def _make_slow_model_step(
    started: asyncio.Event, response: ModelResponse
) -> Callable[[list[ModelMessage]], Awaitable[ModelResponse]]:
    """慢模型响应脚本元素: 先报开始再睡 30s (模拟超长模型调用)."""

    async def slow_step(messages: list[ModelMessage]) -> ModelResponse:
        started.set()
        await asyncio.sleep(30)
        return response

    return slow_step


ECHO_TOOL = tool(_echo, name="echo")
TOOL_CALL_STEP = tool_call_response(make_tool_call("echo", '{"message": "x"}'))


# ---------------------------------------------------------------------------
# LoopGuard 纯单元判定 (端到端用例覆盖行为, 这里锁死精确边界与优先级)
# ---------------------------------------------------------------------------


def test_check_after_turn_boundary_is_inclusive() -> None:
    """边界语义: 恰好等于上限即触发 (>= 而非 >), 差 1 不触发.

    端到端用例只能测到「超限」, 测不出「恰好等于」这个语义选择。
    """
    guard = LoopGuard(max_turns=3, max_total_tokens=100)
    guard.start()

    assert guard.check_after_turn(turn_count=2, total_tokens=99) is None
    assert guard.check_after_turn(turn_count=3, total_tokens=0) is LoopOutcome.MAX_TURNS
    assert (
        guard.check_after_turn(turn_count=2, total_tokens=100)
        is LoopOutcome.TOKEN_BUDGET
    )


def test_check_after_turn_priority_order() -> None:
    """三种限制同时命中时的判定优先级: max_turns > token 预算 > wall-clock."""
    everything = LoopGuard(max_turns=1, max_total_tokens=1, max_duration_seconds=1e-9)
    everything.start()
    assert (
        everything.check_after_turn(turn_count=5, total_tokens=999)
        is LoopOutcome.MAX_TURNS
    )

    no_turns = LoopGuard(max_turns=99, max_total_tokens=1, max_duration_seconds=1e-9)
    no_turns.start()
    assert (
        no_turns.check_after_turn(turn_count=1, total_tokens=1)
        is LoopOutcome.TOKEN_BUDGET
    )


def test_check_after_turn_unlimited_when_none() -> None:
    """未配置的维度不参与判定 (None = 不限制), 只有 max_turns 兜底."""
    guard = LoopGuard(max_turns=10)  # 另两项默认 None
    guard.start()

    assert guard.check_after_turn(turn_count=9, total_tokens=10**9) is None
    assert guard.check_after_turn(turn_count=10, total_tokens=10**9) is (
        LoopOutcome.MAX_TURNS
    )


def test_time_limit_boundary_with_injected_clock() -> None:
    """wall-clock 边界: 恰好等于预算即触发 (固定时钟, 零抖动)."""
    clock = _FakeClock()
    guard = LoopGuard(max_duration_seconds=5.0, _time_source=clock)
    guard.start()

    clock.now = 4.9
    assert guard.check_after_turn(turn_count=1, total_tokens=0) is None
    clock.now = 5.0
    assert (
        guard.check_after_turn(turn_count=1, total_tokens=0) is LoopOutcome.TIME_LIMIT
    )


def test_elapsed_ms_zero_before_start() -> None:
    """未 start() 时 elapsed_ms 恒 0 (docstring 契约, 防未初始化计时被误用)."""
    guard = LoopGuard()
    assert guard.elapsed_ms == 0.0


def test_guard_rejects_non_positive_bounds() -> None:
    """非法上限一律构造期报错 (fail fast, 不静默放行)."""
    with pytest.raises(GuardConfigError, match="max_turns"):
        LoopGuard(max_turns=0)
    with pytest.raises(GuardConfigError, match="max_total_tokens"):
        LoopGuard(max_total_tokens=0)
    with pytest.raises(GuardConfigError, match="max_duration_seconds"):
        LoopGuard(max_duration_seconds=0)


async def test_guard_timer_resets_between_runs() -> None:
    """复用同一 guard 实例: 每次 run 重新 start() 计时, 上一 run 耗时不计入.

    若 start() 未重新调用, 第二次 run 会带着第一次的耗时直接撞上时长预算。
    """
    clock = _FakeClock()
    guard = LoopGuard(max_duration_seconds=0.05, _time_source=clock)

    async def slow_step(messages: list[ModelMessage]) -> ModelResponse:
        """脚本元素: 把时钟推到 0.06s (首次 run 耗时超预算)."""
        clock.now = 0.06
        return text_response("第一次")

    model = ScriptedModel(
        [
            slow_step,
            # 第二次 run: 先转一轮工具 —— 走 guard 判定点, 才能真正验证重置
            tool_call_response(make_tool_call("echo", '{"message": "x"}')),
            text_response("第二次"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL], guard=guard)

    first = await loop.run([dict(USER_MSG)])
    assert first.outcome is LoopOutcome.FINISHED
    assert first.elapsed_ms == 60.0  # 固定时钟 → 确定性

    clock.now = 0.10  # 时钟继续走, 但起点应已重置为 0.10
    second = await loop.run([dict(USER_MSG)])
    assert second.outcome is LoopOutcome.FINISHED  # 未带上第一次的耗时
    assert second.turn_count == 2
    assert second.elapsed_ms == 0.0


# ---------------------------------------------------------------------------
# 软限制 1: max_turns
# ---------------------------------------------------------------------------


async def test_max_turns_stops_infinite_tool_loop() -> None:
    """模型永远调工具 → max_turns 用尽即停, 不无限循环 (#3 首项)."""
    model = ScriptedModel([TOOL_CALL_STEP] * 5)  # 脚本比上限长, 验证被拦截
    loop = AgentLoop(
        model=model,
        tools=[ECHO_TOOL],
        guard=LoopGuard(max_turns=2),
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.MAX_TURNS
    assert result.turn_count == 2
    assert len(model.calls) == 2  # 轨迹: 恰好 2 次模型决策, 无第三次
    assert result.content is None  # 未产出最终答案 (被强制停止)
    assert result.finish_reason is FinishReason.TOOL_CALLS  # 停在工具轮

    # 软限制「轮后判断」: 第 2 轮的工具调用已执行完并回填, 历史合法
    # (以 tool 消息收尾, assistant(tool_calls) ↔ tool 配对完整, 无悬挂)
    history = result.messages
    assert history[-1]["role"] == "tool"
    assert history[-2]["role"] == "assistant"
    assert len(history) == 1 + 2 * 2  # user + (assistant + tool) x 2

    # 工具确实执行了两次 (echo 回填了两轮)
    echo_backfills = [m["content"] for m in history if m["role"] == "tool"]
    assert echo_backfills == ["echo:x", "echo:x"]


async def test_max_turns_one_with_direct_answer() -> None:
    """max_turns=1 且模型直接回答 → 自然结束 (非触发)."""
    model = ScriptedModel([text_response("直接回答")])
    loop = AgentLoop(model=model, guard=LoopGuard(max_turns=1))
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.turn_count == 1
    assert result.content == "直接回答"


# ---------------------------------------------------------------------------
# 软限制 2: token 预算
# ---------------------------------------------------------------------------


async def test_token_budget_stops_loop() -> None:
    """每轮 30 token, 预算 50: 两轮后累计 60 超限即停 (TOKEN_BUDGET)."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "a"}'),
                usage=Usage(total_tokens=30),
            ),
            tool_call_response(
                make_tool_call("echo", '{"message": "b"}'),
                usage=Usage(total_tokens=30),
            ),
            text_response("不该被消费"),  # 第三轮被预算拦截, 不应到达
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[ECHO_TOOL],
        guard=LoopGuard(max_turns=10, max_total_tokens=50),
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.TOKEN_BUDGET
    assert result.turn_count == 2
    assert result.total_tokens == 60  # usage 累计如实报告, 超限由 guard 拦截
    assert len(model.calls) == 2  # 第三轮未发起
    # 第二轮工具调用已回填 (预算判定发生在下一轮模型调用之前)
    assert result.messages[-1]["role"] == "tool"


async def test_token_budget_usage_absent_counts_zero() -> None:
    """响应不带 usage (如部分流式) 时计 0, 预算检查不误伤."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("echo", '{"message": "a"}')),
            tool_call_response(make_tool_call("echo", '{"message": "b"}')),
            text_response("完成"),
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[ECHO_TOOL],
        guard=LoopGuard(max_total_tokens=100),
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.total_tokens == 0


# ---------------------------------------------------------------------------
# 软限制 3: wall-clock
# ---------------------------------------------------------------------------


async def test_wall_clock_stops_loop() -> None:
    """模型响应耗时超过时长预算 → TIME_LIMIT (固定时钟注入, 零抖动)."""
    clock = _FakeClock()

    async def slow_step(messages: list[ModelMessage]) -> ModelResponse:
        """脚本元素: 把时钟推到 0.06s (模拟单轮模型响应耗时 > 0.05s 预算)."""
        clock.now = 0.06
        return TOOL_CALL_STEP

    model = ScriptedModel([slow_step])
    loop = AgentLoop(
        model=model,
        tools=[ECHO_TOOL],
        guard=LoopGuard(max_duration_seconds=0.05, _time_source=clock),
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.TIME_LIMIT
    assert result.turn_count == 1
    assert result.elapsed_ms == 60.0  # 固定时钟 → 确定性断言 (非 >= 抖动阈值)
    assert result.messages[-1]["role"] == "tool"


# ---------------------------------------------------------------------------
# kill switch (asyncio.Task.cancel 即时打断)
# ---------------------------------------------------------------------------


async def test_kill_switch_cancels_running_tool_immediately() -> None:
    """kill switch: 工具执行中 cancel → CancelledError 立即传播, 不等工具跑完.

    与软限制的区别 (difficulties #3 原文): 软限制「这一轮结束后才判断」,
    kill switch「立刻打断」—— 本测试断言打断耗时远小于慢工具 (30s) 的执行
    时长, 且 loop 不吞异常、不伪造结果.
    """
    started = asyncio.Event()
    loop = AgentLoop(
        model=ScriptedModel([tool_call_response(make_tool_call("slow", "{}"))]),
        tools=[_make_slow_tool(started)],
    )
    task = asyncio.create_task(loop.run([dict(USER_MSG)]))

    await started.wait()  # 工具已进入执行 (30s 睡眠中)
    t0 = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 即时性: 打断耗时 << 工具自身 30s (留 2s 宽裕防调度抖动)
    assert time.perf_counter() - t0 < 2

    # 无后台任务泄漏 (取消传播干净, gather 已取消全部子任务)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert pending == []


async def test_kill_switch_during_model_call() -> None:
    """kill switch 在模型调用中 cancel: 同样的即时传播语义."""
    started = asyncio.Event()
    loop = AgentLoop(
        model=ScriptedModel([_make_slow_model_step(started, text_response("太迟了"))])
    )
    task = asyncio.create_task(loop.run([dict(USER_MSG)]))

    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
