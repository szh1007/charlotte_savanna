"""熔断闸测试 (difficulties #14): 三态 + 计数 + 单探测 + 注入时钟.

场景 -> 断言 (全部零真实等待: 时钟注入, 不 sleep 一秒):
- 连续失败到阈值才跳闸 (差一次不算), 成功把连续计数清零 (「连续」二字落在实处)
- 打开期间 acquire 抛 CircuitOpenError, **那一次调用不会被发出去** (由 failover 侧
  的假模型计数钉住, 见 test_retry_failover.py; 这里钉的是闸自己的裁决)
- 冷却期到 -> 半开; 半开**只放一个** (第二个 acquire 立刻被拒)
- 探测成功 -> 合闸 (连续计数清零); 探测失败 -> 回到打开, 冷却从头再数
- 不算故障的失败 (默认判据外, 如参数错) 不计数也不跳闸 —— 但探测位要还回来
- 取消 (record_abort) 不算失败: 只还探测位 (否则半开态卡死, 那个模型再也放不出请求)
- 阈值 / 冷却取值非法 -> 构造期 RetryConfigError
- 开与关的痕迹落日志 (闸跳 / 闸合各一行, C05 的结构化出口)
"""

from __future__ import annotations

import pytest
from doubles import FakeClock

from CharAgent.model.utils.errors import ModelStatusError
from CharAgent.retry import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitPolicy,
    CircuitState,
)
from CharAgent.retry.utils.errors import RetryConfigError


def _params_error() -> ModelStatusError:
    """400 参数错 (永久, 不算这个模型的账) —— 与 429 相对照的那个样本."""
    return ModelStatusError(400, "bad request", kind=None)


def _server_error() -> ModelStatusError:
    """500 服务端故障 (瞬态, 算账) —— 熔断判据默认只认这一族."""
    return ModelStatusError(503, "boom")


def _breaker(clock: FakeClock, **overrides: object) -> CircuitBreaker:
    """默认样本: 连错 3 次跳闸 / 冷却 30 秒 / 固定时钟 (要别的就覆盖)."""
    policy: dict[str, object] = {
        "failure_threshold": 3,
        "cooldown_seconds": 30.0,
        "time_source": clock,
    }
    policy.update(overrides)
    return CircuitBreaker(
        "deepseek-flash",
        policy=CircuitPolicy(**policy),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 计数与跳闸
# ---------------------------------------------------------------------------


def test_a_fresh_breaker_is_closed_and_lets_everything_through() -> None:
    breaker = _breaker(FakeClock())

    assert breaker.state is CircuitState.CLOSED
    assert breaker.failures == 0
    breaker.acquire()  # 不抛 = 放行
    breaker.acquire()  # 关闭态不占位, 来多少放多少


def test_it_trips_only_on_the_nth_consecutive_failure() -> None:
    breaker = _breaker(FakeClock())

    assert breaker.record_failure(_server_error()) is False
    assert breaker.record_failure(_server_error()) is False
    assert breaker.state is CircuitState.CLOSED, "差一次不该跳"
    assert breaker.failures == 2

    assert breaker.record_failure(_server_error()) is True, "第三次: 这一下把闸拨开了"
    assert breaker.state is CircuitState.OPEN
    assert breaker.failures == 3


def test_a_success_resets_the_consecutive_count() -> None:
    breaker = _breaker(FakeClock())
    breaker.record_failure(_server_error())
    breaker.record_failure(_server_error())

    breaker.acquire()
    breaker.record_success()

    assert breaker.failures == 0
    breaker.record_failure(_server_error())
    assert breaker.state is CircuitState.CLOSED, "清零之后要重新数到 3 才跳"


def test_a_failure_that_is_not_this_models_fault_does_not_count() -> None:
    breaker = _breaker(FakeClock())

    # 400 参数错: 换个模型发同样的请求一样会错 —— 拿它跳闸毫无意义
    assert breaker.record_failure(_params_error()) is False
    assert breaker.record_failure(_params_error()) is False
    assert breaker.record_failure(_params_error()) is False

    assert breaker.failures == 0
    assert breaker.state is CircuitState.CLOSED


# ---------------------------------------------------------------------------
# 打开: 真的不发 (闸自己的裁决)
# ---------------------------------------------------------------------------


def test_while_open_every_request_is_refused_with_a_readable_reason() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure(_server_error())

    with pytest.raises(CircuitOpenError) as caught:
        breaker.acquire()

    message = str(caught.value)
    assert "deepseek-flash" in message
    assert "连续 3 次失败" in message
    assert "冷却还需 30 秒" in message
    assert "没有发出去" in message
    assert caught.value.name == "deepseek-flash"


def test_a_refused_call_is_not_retryable() -> None:
    """熔断拒发必须是**不可重试**的: 重试层据此立刻放弃, 不在退避里空转 (#13 判据)."""
    from CharAgent.retry import is_retryable

    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure(_server_error())

    with pytest.raises(CircuitOpenError) as caught:
        breaker.acquire()

    assert is_retryable(caught.value) is False


def test_cooldown_is_measured_from_the_trip_moment() -> None:
    clock = FakeClock()
    breaker = _breaker(clock, cooldown_seconds=30.0)
    for _ in range(3):
        breaker.record_failure(_server_error())

    clock.now += 29.9
    assert breaker.state is CircuitState.OPEN

    clock.now += 0.1  # 恰好到点: 半开 (够判「该放探测了」)
    assert breaker.state is CircuitState.HALF_OPEN


# ---------------------------------------------------------------------------
# 半开: 只放一个
# ---------------------------------------------------------------------------


def _tripped(clock: FakeClock, **overrides: object) -> CircuitBreaker:
    """造一个刚跳到打开态的闸 (连错到默认阈值 3 次), 供半开那几条用例起步."""
    breaker = _breaker(clock, **overrides)
    for _ in range(3):
        breaker.record_failure(_server_error())
    assert breaker.state is CircuitState.OPEN
    return breaker


def test_half_open_lets_exactly_one_probe_through() -> None:
    clock = FakeClock()
    breaker = _tripped(clock)
    clock.now += 30.0  # 冷却到点

    assert breaker.state is CircuitState.HALF_OPEN
    breaker.acquire()  # 第一个: 拿到探测位

    with pytest.raises(CircuitOpenError) as caught:
        breaker.acquire()  # 第二个: 被拒 —— 否则积压的请求会把刚缓过来的下游再打挂

    assert "探测" in str(caught.value)


def test_a_successful_probe_closes_the_breaker() -> None:
    clock = FakeClock()
    breaker = _tripped(clock)
    clock.now += 30.0

    breaker.acquire()
    breaker.record_success()

    assert breaker.state is CircuitState.CLOSED
    assert breaker.failures == 0
    breaker.acquire()  # 合闸之后不再占位, 来多少放多少
    breaker.acquire()


def test_a_failed_probe_reopens_and_restarts_the_cooldown() -> None:
    clock = FakeClock()
    breaker = _tripped(clock)
    clock.now += 30.0
    breaker.acquire()

    assert breaker.record_failure(_server_error()) is True

    assert breaker.state is CircuitState.OPEN
    clock.now += 29.0
    assert breaker.state is CircuitState.OPEN, "冷却从头再数"
    clock.now += 1.0
    assert breaker.state is CircuitState.HALF_OPEN


def test_a_cancelled_probe_gives_the_slot_back() -> None:
    """取消不算失败, 但探测位**必须**还回来 —— 不还的话半开态卡死, 再没人放得出去."""
    clock = FakeClock()
    breaker = _tripped(clock)
    clock.now += 30.0

    breaker.acquire()
    breaker.record_abort()

    assert breaker.failures == 3, "取消不是上游的病, 不该记到账上"
    assert breaker.state is CircuitState.HALF_OPEN, "还回探测位之后仍等着人探路"
    breaker.acquire()  # 探测位回来了: 下一个调用能拿到


def test_a_cancelled_call_while_closed_changes_nothing() -> None:
    breaker = _breaker(FakeClock())

    breaker.acquire()
    breaker.record_abort()

    assert breaker.state is CircuitState.CLOSED
    assert breaker.failures == 0


# ---------------------------------------------------------------------------
# 迟到的结论 (并发调用才会遇到): 在飞的调用回来了, 而闸已经被别人拨开
# ---------------------------------------------------------------------------


def test_a_late_failure_does_not_push_the_cooldown_back() -> None:
    """跳闸之前发出去的那次调用失败得晚: 不记账, 也不把冷却期从头再数.

    为什么这条要紧: 一次调用发出去之后, 别的调用可能已经把闸拨开了 —— 它的失败在
    闸开**之后**才回来. 若照记, `_trip()` 会刷新跳闸时刻, 于是「刚被前一批失败
    拨开的闸」因为一个更早的失败多关一整个冷却期; 并发一多, 冷却期会被无限推后
    (每一次迟到失败都再推一轮).
    """
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure(_server_error())
    assert breaker.state is CircuitState.OPEN

    clock.now = 10.0
    assert breaker.record_failure(_server_error()) is False, "迟到的那次不算账"

    assert breaker.failures == 3, "停在阈值上 (不越数越大)"
    clock.now = 30.0
    assert breaker.state is CircuitState.HALF_OPEN, "冷却仍从跳闸那一刻起算"
    breaker.acquire(), "半开照常放一个探测"


def test_a_late_success_does_not_reopen_the_gate() -> None:
    """同理: 迟到的成功不能拿来合闸 —— 一个旧结论推翻一串新证据."""
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure(_server_error())

    breaker.record_success()

    assert breaker.state is CircuitState.OPEN, "闸还开着"
    assert breaker.failures == 3


def test_the_probe_is_still_the_one_that_decides() -> None:
    """探测 (半开时放出去的那一个) 的结论照记 —— 「迟到」的判据不会误伤它."""
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure(_server_error())
    clock.now = 30.0
    breaker.acquire()  # 占住探测位

    assert breaker.record_failure(_server_error()) is True, "探测失败 -> 回打开"
    assert breaker.state is CircuitState.OPEN


# ---------------------------------------------------------------------------
# 配置校验与痕迹
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [("failure_threshold", 0), ("failure_threshold", -1), ("cooldown_seconds", 0.0)],
)
def test_illegal_config_fails_fast(field: str, value: object) -> None:
    """阈值 / 冷却非法: 构造期就报 (规矩本自己校验, 不等到运行期去猜)."""
    with pytest.raises(RetryConfigError):
        CircuitPolicy(**{field: value})  # type: ignore[arg-type]


def test_the_definition_and_the_resolution_are_separate() -> None:
    """规矩本可以**先造后配** (与 RetryPolicy 同款的使用法): 定义一份, 几个闸共用."""
    policy = CircuitPolicy(
        failure_threshold=1, cooldown_seconds=5.0, time_source=FakeClock()
    )

    first = CircuitBreaker("主", policy=policy)
    second = CircuitBreaker("备", policy=policy)

    assert first.record_failure(_server_error()) is True, "阈值 1: 第一次失败就跳"
    assert second.state is CircuitState.CLOSED, "另一家的闸自己那本账"
    assert first.policy is policy


def test_the_trip_and_the_recovery_leave_a_line_in_the_log(log_stream) -> None:
    """开 / 关的痕迹落日志 (C05 之后的统一出口): 跳闸一行警告, 合闸一行信息."""
    clock = FakeClock()
    breaker = _breaker(clock)

    for _ in range(3):
        breaker.record_failure(_server_error())
    clock.now += 30.0
    breaker.acquire()
    breaker.record_success()

    written = log_stream.getvalue()
    assert "熔断跳闸" in written
    assert "连续 3 次失败" in written
    assert "熔断解除" in written
