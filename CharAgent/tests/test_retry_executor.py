"""通用重试驱动器测试 (issue 06 / difficulties #13): 异常路径 + 上限 + 回调.

场景 → 断言:
- 首次成功: 不重试 / 不等待 / 不通知 (零开销路径)
- 瞬态失败 (429): 按退避序列等待后重试, 成功即返回; on_retry 载荷逐字段保真
  (第几次 / 等多久 / 已耗时 / 原因 / 原始异常)
- 永久失败 (4xx): 立即上抛, 不等待不重试 (重试一百次也一样)
- 尝试耗尽: 抛**最后一个**异常 (原始异常对象原样上抛, 异常链保真)
- 耗时预算: 本次等待会撞破总预算就不再等, 提前放弃
- 响应不合格: 结果被判据拒绝时同样重试; 耗尽返回**最后一个结果** (不伪造
  异常 —— 上游中断重试耗尽后, loop 仍该看到那个半截响应并如实上报)
- kill switch: CancelledError 不重试不吞 (重试不得挡住取消, #3)
- 回调: on_retry 收**序列**, 多个回调按序列顺序依次调用 (同步 / 异步混用均可;
  时序: 通知 → 等待 → 再试); 回调异常向上传播 (对齐 stream 包的 event_sink)

睡眠 / 时钟 / 随机源全部注入 (tests/doubles.py): 断言零真实等待且完全确定
(#61).
"""

from __future__ import annotations

import asyncio

import pytest
from doubles import FakeClock, RecordingSleep

from CharAgent.model import ModelStatusError
from CharAgent.retry import (
    RetryAttempt,
    RetryConfigError,
    RetryPolicy,
    retry_async,
)


def _policy(**overrides: object) -> RetryPolicy:
    """默认测试策略: 不抖动 (确定性) + 注入时钟与记录式睡眠 (零真实等待)."""
    kwargs: dict[str, object] = {
        "jitter": 0.0,
        "initial_delay": 0.5,
        "multiplier": 2.0,
        "max_delay": 30.0,
        "max_elapsed_seconds": 60.0,
    }
    kwargs.update(overrides)
    return RetryPolicy(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 成功路径与瞬态重试
# ---------------------------------------------------------------------------


async def test_returns_immediately_when_operation_succeeds() -> None:
    """首次成功: 不等待、不通知 (零开销路径)."""
    sleep = RecordingSleep()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    attempts: list[RetryAttempt] = []
    result = await retry_async(
        operation, policy=_policy(sleep=sleep), on_retry=[attempts.append]
    )

    assert result == "ok"
    assert calls == 1
    assert sleep.delays == []
    assert attempts == []


async def test_retries_transient_failure_then_succeeds() -> None:
    """429 失败一次后成功: 按退避等待一次, on_retry 载荷逐字段保真 (#13)."""
    clock = FakeClock()
    sleep = RecordingSleep(clock)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            clock.now += 0.2  # 模拟这次调用本身耗时 0.2s
            raise ModelStatusError(429, "rate limit exceeded")
        return "ok"

    attempts: list[RetryAttempt] = []
    result = await retry_async(
        operation,
        policy=_policy(sleep=sleep, time_source=clock),
        on_retry=[attempts.append],
    )

    assert result == "ok"
    assert calls == 2
    assert sleep.delays == [0.5]  # 退避序列第 1 项
    assert len(attempts) == 1
    (record,) = attempts
    assert record.attempt == 1
    assert record.delay == 0.5
    assert record.elapsed == pytest.approx(0.2)  # 首次调用耗时, 不含等待
    assert "429" in record.reason
    assert isinstance(record.error, ModelStatusError)
    assert record.result is None


async def test_permanent_failure_propagates_without_retrying() -> None:
    """4xx 永久错误: 立即上抛, 不等待不重试 (重试一百次也一样)."""
    sleep = RecordingSleep()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ModelStatusError(400, "bad request")

    attempts: list[RetryAttempt] = []
    with pytest.raises(ModelStatusError) as exc_info:
        await retry_async(
            operation, policy=_policy(sleep=sleep), on_retry=[attempts.append]
        )

    assert exc_info.value.status_code == 400
    assert calls == 1
    assert sleep.delays == []
    assert attempts == []


async def test_exhausted_attempts_raise_the_last_error() -> None:
    """重试到上限: 抛**最后一个**异常 (不是第一个), 等待序列 = 退避序列."""
    clock = FakeClock()
    sleep = RecordingSleep(clock)
    errors: list[ModelStatusError] = []

    async def operation() -> str:
        error = ModelStatusError(503, f"unavailable #{len(errors)}")
        errors.append(error)
        raise error

    attempts: list[RetryAttempt] = []
    with pytest.raises(ModelStatusError) as exc_info:
        await retry_async(
            operation,
            policy=_policy(sleep=sleep, time_source=clock, max_attempts=3),
            on_retry=[attempts.append],
        )

    assert exc_info.value is errors[-1]  # 最后一个异常原样上抛 (不包装)
    assert len(errors) == 3
    assert sleep.delays == [0.5, 1.0]
    assert [record.attempt for record in attempts] == [1, 2]


async def test_elapsed_budget_gives_up_before_sleeping() -> None:
    """耗时预算: 本次等待会撞破总预算就不再等 (等待只是白等)."""
    clock = FakeClock()
    sleep = RecordingSleep(clock)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        clock.now += 1.0  # 每次调用耗时 1s
        raise ModelStatusError(500, "internal error")

    policy = _policy(
        sleep=sleep,
        time_source=clock,
        max_attempts=5,
        initial_delay=4.0,  # 第 1 次退避 4s: 1.0 + 4.0 <= 5.0 恰好挤下
        max_elapsed_seconds=5.0,
    )
    with pytest.raises(ModelStatusError):
        await retry_async(operation, policy=policy)

    # 尝试 1 失败 (elapsed 1.0) -> 等 4s; 尝试 2 失败 (elapsed 6.0) ->
    # 再等就超预算 (6.0 + 8.0 > 5.0) -> 放弃, 共 2 次尝试
    assert calls == 2
    assert sleep.delays == [4.0]


# ---------------------------------------------------------------------------
# 响应不合格路径 (上游中断: 结果不合格也重试, #13 双计费的暴露点)
# ---------------------------------------------------------------------------


async def test_retry_on_result_retries_unacceptable_result() -> None:
    """结果被拒判据判为不合格 -> 同样走退避重试; 记录单带原样结果与拒绝原因.

    模型场景: finish_reason=insufficient_system_resource (资源不足) 时官方
    指引「稍后重试」, 但那一次**已经计费** —— 原样结果随 RetryAttempt 交给
    调用方, 供 P1-11 把烧掉的 token 记账 (#13).
    """
    clock = FakeClock()
    sleep = RecordingSleep(clock)
    responses = ["半截答复", "完整答复"]

    async def operation() -> str:
        return responses.pop(0)

    def reject(text: str) -> str | None:
        """None = 可接受; 非空字符串 = 不合格并作为重试原因."""
        return None if text == "完整答复" else "上游中断, 内容只有半截"

    attempts: list[RetryAttempt] = []
    result = await retry_async(
        operation,
        policy=_policy(sleep=sleep, time_source=clock),
        retry_on_result=reject,
        on_retry=[attempts.append],
    )

    assert result == "完整答复"
    assert sleep.delays == [0.5]
    (record,) = attempts
    assert record.result == "半截答复"  # 原样返回值 (可读其 usage)
    assert record.error is None  # 响应路径没有异常
    assert record.reason == "上游中断, 内容只有半截"


async def test_retry_on_result_exhausted_returns_the_last_result() -> None:
    """耗尽: 返回**最后一个**结果, 不抛异常 (loop 仍该看到半截响应如实上报)."""
    sleep = RecordingSleep()

    async def operation() -> str:
        return "始终半截"

    attempts: list[RetryAttempt] = []
    result = await retry_async(
        operation,
        policy=_policy(sleep=sleep, max_attempts=2),
        retry_on_result=lambda text: "上游中断" if text == "始终半截" else None,
        on_retry=[attempts.append],
    )

    assert result == "始终半截"
    assert sleep.delays == [0.5]  # 共 2 次尝试 = 1 次重试
    assert [record.attempt for record in attempts] == [1]


# ---------------------------------------------------------------------------
# kill switch
# ---------------------------------------------------------------------------


async def test_cancelled_error_is_not_retried_or_swallowed() -> None:
    """CancelledError 直接传播: 不重试、不等待、不吞 (重试不得挡住取消, #3)."""
    sleep = RecordingSleep()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await retry_async(operation, policy=_policy(sleep=sleep))

    assert calls == 1
    assert sleep.delays == []


# ---------------------------------------------------------------------------
# 重试通知回调
# ---------------------------------------------------------------------------


async def test_retry_callback_is_called_before_sleeping() -> None:
    """回调时序: 通知 (拿到等待秒数) 在等待之前, 之后才是下一次尝试."""
    order: list[str] = []
    recorded = RecordingSleep()

    async def sleep(seconds: float) -> None:
        order.append("sleep")
        await recorded(seconds)

    async def notify(record: RetryAttempt) -> None:
        order.append(f"notify:{record.attempt}:{record.delay}")

    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        order.append(f"call:{calls}")
        if calls == 1:
            raise ModelStatusError(503, "unavailable")
        return "ok"

    result = await retry_async(
        operation, policy=_policy(sleep=sleep), on_retry=[notify]
    )

    assert result == "ok"
    assert order == ["call:1", "notify:1:0.5", "sleep", "call:2"]
    assert recorded.delays == [0.5]


async def test_retry_callback_error_propagates() -> None:
    """回调异常向上传播 (调用方自己的回调, 通知失败要让它看见)."""
    sleep = RecordingSleep()

    async def operation() -> str:
        raise ModelStatusError(503, "unavailable")

    def broken_notify(record: RetryAttempt) -> None:
        raise RuntimeError("遥测管道坏了")

    with pytest.raises(RuntimeError, match="遥测管道坏了"):
        await retry_async(
            operation, policy=_policy(sleep=sleep), on_retry=[broken_notify]
        )

    assert sleep.delays == []  # 通知失败即中止: 没有继续等待与重试


# ---------------------------------------------------------------------------
# 多回调 (on_retry 收序列): 顺序执行 / 不隔离 / 空序列零开销
# ---------------------------------------------------------------------------


async def test_multiple_retry_callbacks_all_fire_in_order() -> None:
    """on_retry 收序列: 多个回调按顺序依次调用, 同步/异步混用都接受."""
    order: list[str] = []
    sleep = RecordingSleep()

    def sync_callback(record: RetryAttempt) -> None:
        order.append(f"sync:{record.attempt}")

    async def async_callback(record: RetryAttempt) -> None:
        await asyncio.sleep(0)  # 真异步: 证明它被 await 到位
        order.append(f"async:{record.attempt}")

    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelStatusError(503, "unavailable")
        return "ok"

    result = await retry_async(
        operation,
        policy=_policy(sleep=sleep),
        on_retry=[sync_callback, async_callback],
    )

    assert result == "ok"
    assert order == ["sync:1", "async:1"]  # 顺序 = 序列顺序, 串行 await


async def test_callback_failure_skips_the_remaining_callbacks() -> None:
    """回调中途抛异常: 后面的回调不再执行, 异常向上传播 (不做隔离, 见 docstring).

    与 hooks 注册表的插件隔离语义相反 —— on_retry 是调用方自己的观测链,
    某一环坏了要让它看见, 而不是静默跳过 (静默会让账目悄悄少记一笔).
    """
    ran: list[str] = []
    sleep = RecordingSleep()

    def broken(record: RetryAttempt) -> None:
        raise RuntimeError("观测管道坏了")

    def never_called(record: RetryAttempt) -> None:
        ran.append("never")

    async def operation() -> str:
        raise ModelStatusError(503, "unavailable")

    with pytest.raises(RuntimeError, match="观测管道坏了"):
        await retry_async(
            operation, policy=_policy(sleep=sleep), on_retry=[broken, never_called]
        )

    assert ran == []
    assert sleep.delays == []  # 中止在等待之前


async def test_bare_callback_is_rejected_at_entry() -> None:
    """传裸函数 (改造前的写法) 在**入口**即报错并提示改成 ``[callback]``.

    若不做这道护栏, 裸函数只在「真的发生重试」时才炸出
    ``TypeError: 'function' object is not iterable`` —— 而首轮就成功、或
    max_attempts=1 的运行根本走不到通知处, 传错的参数会被静默吞掉.
    """
    sleep = RecordingSleep()

    async def operation() -> str:
        return "ok"

    with pytest.raises(RetryConfigError, match=r"\[callback\]"):
        await retry_async(
            operation,
            policy=_policy(sleep=sleep),
            on_retry=lambda record: None,  # type: ignore[arg-type]
        )


async def test_empty_callback_sequence_is_a_no_op() -> None:
    """空序列 = 不挂观测: 重试照常发生 (观测可选, 且零开销)."""
    sleep = RecordingSleep()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelStatusError(503, "unavailable")
        return "ok"

    result = await retry_async(operation, policy=_policy(sleep=sleep), on_retry=[])

    assert result == "ok"
    assert sleep.delays == [0.5]
