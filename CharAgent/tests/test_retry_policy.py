"""重试策略测试 (difficulties #13): 退避序列 + 上限裁决 + 瞬态判据.

场景 → 断言:
- 指数退避: jitter=0 时退避序列 = initial_delay * multiplier^(n-1); 越过
  max_delay 后封顶 (退避不无限增长, 否则第 10 次失败要等到天亮)
- jitter: 等待值随注入的随机源线性缩放, 永不越过该次退避基准 (散开惊群,
  但不把等待拉长); jitter=0 时完全不消费随机源 (确定性, #61)
- 上限裁决 (next_delay): 尝试次数用尽 -> None; 本次等待会撞破总耗时预算 ->
  None (等待只是白等); 预算为 None 表示不限制
- 配置校验: 非正上限 / 因子小于 1 / 抖动越界 -> RetryConfigError (构造期
  fail fast, 不把坏配置带到运行期)
- 瞬态判据: 异常自带 retryable=True 才可重试 (429/5xx/超时); 未声明该属性
  的异常一律不重试 (4xx / 工具错误 / 编程错误)

随机源与时钟全部注入, 断言精确到约定值 (#61 确定性的做法, 对齐
test_loop_guard.py 的固定时钟).
"""

from __future__ import annotations

import pytest
from doubles import FixedRandom

from CharAgent.model import (
    ModelConnectionError,
    ModelProtocolError,
    ModelStatusError,
    ModelTimeoutError,
)
from CharAgent.retry import RetryConfigError, RetryPolicy, is_retryable
from CharAgent.tool import ToolActionableError


class _ExplodingRandom:
    """被调用即失败: 证明 jitter=0 时策略根本不消费随机数."""

    def __call__(self) -> float:
        raise AssertionError("jitter=0 时不应消费随机源")


# ---------------------------------------------------------------------------
# 退避序列: 指数增长 + 单次封顶 + 抖动
# ---------------------------------------------------------------------------


def test_backoff_sequence_is_exponential_without_jitter() -> None:
    """jitter=0: 退避序列 = initial_delay * multiplier^(n-1) (指数退避, #13)."""
    policy = RetryPolicy(initial_delay=0.5, multiplier=2.0, max_delay=30.0, jitter=0.0)

    assert [policy.backoff(attempt) for attempt in (1, 2, 3, 4)] == [
        0.5,
        1.0,
        2.0,
        4.0,
    ]


def test_backoff_is_capped_by_max_delay() -> None:
    """单次退避封顶: 指数增长到 max_delay 后不再变长 (上限可控)."""
    policy = RetryPolicy(initial_delay=0.5, multiplier=2.0, max_delay=3.0, jitter=0.0)

    assert [policy.backoff(attempt) for attempt in (1, 2, 3, 4, 5)] == [
        0.5,
        1.0,
        2.0,
        3.0,
        3.0,
    ]


def test_backoff_jitter_scales_within_the_base_delay() -> None:
    """抖动注入 (#61): 等待 = base * (1 - jitter * u), 永不越过基准 base.

    随机源固定 → 断言可算出的等待值; 抖动只用来打散 (惊群), 不把等待拉长 ——
    故 u 取 0.9 (几乎最大抖动) 时等待仍 <= base, 不会出现「退避比上一档还久」
    的错位.

    等待值用 approx 断言: 退避是浮点乘法 (8.0 * 0.1 = 0.7999999999999998),
    断言的是语义值而非浮点位模式.
    """
    policy = RetryPolicy(
        initial_delay=2.0,
        multiplier=2.0,
        max_delay=30.0,
        jitter=1.0,
        random_source=FixedRandom(0.0, 0.5, 0.9),
    )

    delays = [policy.backoff(attempt) for attempt in (1, 2, 3)]

    assert delays == pytest.approx([2.0, 2.0, 0.8])
    assert max(delays) <= policy.max_delay


def test_backoff_with_zero_jitter_does_not_consume_randomness() -> None:
    """jitter=0: 完全不消费随机源 (确定性退避, #61 —— 随机源一碰就报错)."""
    policy = RetryPolicy(jitter=0.0, random_source=_ExplodingRandom())

    assert policy.backoff(attempt=1) == policy.initial_delay


# ---------------------------------------------------------------------------
# 上限裁决 (next_delay): 还试不试, 试的话等多久
# ---------------------------------------------------------------------------


def test_next_delay_stops_at_max_attempts() -> None:
    """尝试次数用尽 -> None (第 max_attempts 次失败后不再有下一次)."""
    policy = RetryPolicy(
        max_attempts=3,
        initial_delay=0.5,
        multiplier=2.0,
        max_delay=30.0,
        jitter=0.0,
        max_elapsed_seconds=None,
    )

    assert policy.next_delay(attempt=1, elapsed=0.0) == 0.5
    assert policy.next_delay(attempt=2, elapsed=0.0) == 1.0
    assert policy.next_delay(attempt=3, elapsed=0.0) is None


def test_next_delay_stops_when_waiting_would_break_elapsed_budget() -> None:
    """总耗时预算: 本次等待已会撞破预算就不再等 (等待只是白等).

    边界语义: ``elapsed + delay > max_elapsed_seconds`` 才放弃 —— 恰好等于
    预算时仍允许等待 (下一次判定再收口), 与 LoopGuard 的「恰好等于即触发」
    相反, 因为这里是**预判**而不是事后判定: 预判应按「还能不能挤出这段时间」
    理解.
    """
    policy = RetryPolicy(
        max_attempts=5,
        initial_delay=4.0,
        multiplier=2.0,
        max_delay=30.0,
        jitter=0.0,
        max_elapsed_seconds=5.0,
    )

    assert policy.next_delay(attempt=1, elapsed=1.0) == 4.0  # 1.0 + 4.0 == 预算
    assert policy.next_delay(attempt=1, elapsed=1.2) is None  # 1.2 + 4.0 > 预算


def test_next_delay_ignores_elapsed_when_budget_is_none() -> None:
    """预算 None = 不限制耗时: 只剩尝试次数兜底 (上限可控的另一半)."""
    policy = RetryPolicy(
        max_attempts=2, initial_delay=0.5, jitter=0.0, max_elapsed_seconds=None
    )

    assert policy.next_delay(attempt=1, elapsed=10**9) == 0.5
    assert policy.next_delay(attempt=2, elapsed=0.0) is None


# ---------------------------------------------------------------------------
# 配置校验 (构造期 fail fast)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"max_attempts": 0}, "max_attempts"),
        ({"initial_delay": -0.1}, "initial_delay"),
        ({"multiplier": 0.5}, "multiplier"),
        ({"max_delay": 0}, "max_delay"),
        ({"max_elapsed_seconds": 0}, "max_elapsed_seconds"),
        ({"jitter": 1.5}, "jitter"),
    ],
)
def test_policy_rejects_invalid_bounds(overrides: dict[str, float], field: str) -> None:
    """非法上限 / 因子 / 抖动一律构造期报错, 消息点名出错的字段 (每例一个行为)."""
    with pytest.raises(RetryConfigError, match=field):
        RetryPolicy(**overrides)


def test_policy_accepts_boundary_configurations() -> None:
    """边界合法值: 不等待 / 恒定退避 (multiplier=1) / 全抖动 / 不限制耗时."""
    policy = RetryPolicy(
        initial_delay=0.0,
        multiplier=1.0,
        jitter=1.0,
        max_elapsed_seconds=None,
        random_source=FixedRandom(0.0),
    )

    assert policy.backoff(attempt=5) == 0.0
    assert policy.next_delay(attempt=1, elapsed=0.0) == 0.0


# ---------------------------------------------------------------------------
# 瞬态判据 (默认 is_retryable)
# ---------------------------------------------------------------------------


def test_is_retryable_reads_the_exception_attribute() -> None:
    """默认判据: 异常自带 retryable=True 才可重试 (#13 只重试瞬态)."""
    assert is_retryable(ModelStatusError(429, "rate limit exceeded")) is True
    assert is_retryable(ModelStatusError(503, "service unavailable")) is True
    assert is_retryable(ModelStatusError(400, "bad request")) is False
    assert is_retryable(ModelStatusError(401, "unauthorized")) is False
    assert is_retryable(ModelTimeoutError("请求超时")) is True
    assert is_retryable(ModelConnectionError("连接中断")) is True
    assert is_retryable(ModelProtocolError("响应畸形")) is False


def test_is_retryable_defaults_to_false_for_undeclared_exceptions() -> None:
    """未声明 retryable 的异常一律不重试 (工具错误 / 编程错误: 再试也一样)."""
    assert is_retryable(ToolActionableError("order_no 应为 14 位数字")) is False
    assert is_retryable(ValueError("bug")) is False
