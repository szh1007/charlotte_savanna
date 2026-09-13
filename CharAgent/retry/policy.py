"""RetryPolicy (issue 06 / difficulties #13): 退避策略与上限裁决.

一句话理解: 「失败了要不要再试一次、等多久再试」的规矩. 分两问:

1. **该不该试** —— 只重试「瞬态」失败 (上游 429 限流 / 5xx 故障 / 网络超时),
   参数错这类 4xx 重试一百次也一样 (判据见下方 `is_retryable`);
2. **等多久** —— 指数退避 + jitter: 每失败一次就等得更久 (不给已经吃力的
   上游继续加压), 再叠一点随机抖动 (避免大批请求在同一瞬间一起回头, 即
   「惊群」).

三个上限, 任一命中就不再试 (全部构造期可配, ticket 第 2 条「上限可控」):
- max_attempts:        总尝试次数 (含首次)
- max_elapsed_seconds: 自首次尝试起的总耗时预算 (含调用耗时与等待) ——
                       本次等待已经会撞破预算就不等 (等待只是白等)
- max_delay:           单次等待封顶 (指数增长必须收敛)

**没有熔断**: 上游持续 429 时重试确实会放大压力, 本模块只靠上面三条上限兜底
(次数与总耗时都封死, 不会无限放大); 「别再打给它了」的熔断 + failover 属 P1-3.

三条缝 (注入点, 对齐 LoopGuard.time_source 的惯例; 测试零等待且完全确定 #61):
- sleep:         如何等待 (默认 asyncio.sleep; 测试换成记录式睡眠, 不真等)
- time_source:   怎么读时间 (默认 time.monotonic; 测试换固定时钟)
- random_source: 抖动的随机来源 (默认 random.random; 测试换固定序列)

**无状态**: 与 LoopGuard (每 run 持一个 _started 计时) 不同, RetryPolicy 只装
配置与注入缝, 可跨 run / 并发安全复用 —— 「已耗时」由驱动器每次现算, 不落在
策略对象上 (否则并发重试会互相污染).

大白话版: 这是重试的「规矩本」——写清楚什么错值得再试一次 (瞬态), 每次失败
后等多久 (越等越久 + 随机抖动), 以及什么时候该认输 (试够次数 / 时间用光 /
等待封顶). 规矩本本身不做重试, 干活的是 executor.retry_async.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from CharAgent.retry.utils.errors import RetryConfigError


def is_retryable(exc: BaseException) -> bool:
    """默认瞬态判据: 异常自带 ``retryable=True`` 才算可重试 (#13).

    模型层异常族 (model/utils/errors.py) 已按协议标好整族语义: 429 / 5xx /
    连接失败 / 超时 = True, 其余 4xx / 配置错误 / 响应畸形 = False.

    未声明该属性的异常 (工具错误 / 编程错误 / 第三方库异常) 一律**不重试** ——
    重试是可靠性兜底, 不是「任何异常都再试一次」: 错试 (把必然失败的重试到
    耗尽预算) 比漏试更贵.

    Args:
        exc: 捕获到的异常实例.

    Returns:
        bool: True 表示瞬态, 值得等待后重试.
    """
    return getattr(exc, "retryable", False) is True


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """重试策略配置 (difficulties #13): 退避序列 + 三个上限 + 三条注入缝.

    attributes:
        max_attempts: 总尝试次数上限 (含首次调用), 至少 1.
        initial_delay: 首次退避基准等待 (秒), 0 表示不等待.
        multiplier: 指数增长因子, 至少 1 (1 = 恒定等待).
        max_delay: 单次等待封顶 (秒), 必须为正.
        max_elapsed_seconds: 自首次尝试起的总耗时预算 (秒), None 表示不限制.
        jitter: 抖动比例 [0, 1]: 0 = 纯指数 (确定性), 1 = 全抖动
            (等待均匀落在 (0, base], AWS 推荐的 full jitter).
        time_source: 计时器注入点 (默认 time.monotonic).
        sleep: 等待注入点 (默认 asyncio.sleep).
        random_source: 抖动随机源注入点 (默认 random.random), 返回 [0, 1).
    """

    max_attempts: int = 3
    initial_delay: float = 0.5
    multiplier: float = 2.0
    max_delay: float = 30.0
    max_elapsed_seconds: float | None = 60.0
    jitter: float = 1.0
    time_source: Callable[[], float] = field(default=time.monotonic, repr=False)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)
    random_source: Callable[[], float] = field(default=random.random, repr=False)

    def __post_init__(self) -> None:
        """配置校验: 非法数值构造期报错 (fail fast, 不把坏策略带到运行期).

        Raises:
            RetryConfigError: 某个上限 / 因子 / 抖动比例越界.
        """
        if self.max_attempts < 1:
            raise RetryConfigError(f"max_attempts 必须 >= 1, 实际: {self.max_attempts}")
        if self.initial_delay < 0:
            raise RetryConfigError(
                f"initial_delay 必须 >= 0, 实际: {self.initial_delay}"
            )
        if self.multiplier < 1:
            raise RetryConfigError(f"multiplier 必须 >= 1, 实际: {self.multiplier}")
        if self.max_delay <= 0:
            raise RetryConfigError(f"max_delay 必须 > 0, 实际: {self.max_delay}")
        if self.max_elapsed_seconds is not None and self.max_elapsed_seconds <= 0:
            raise RetryConfigError(
                f"max_elapsed_seconds 必须 > 0 或 None, "
                f"实际: {self.max_elapsed_seconds}"
            )
        if not 0 <= self.jitter <= 1:
            raise RetryConfigError(f"jitter 必须落在 [0, 1], 实际: {self.jitter}")

    def backoff(self, attempt: int) -> float:
        """第 ``attempt`` 次尝试失败后的退避等待值 (秒, attempt 从 1 起).

        指数部分 ``base = min(initial_delay * multiplier ** (attempt - 1),
        max_delay)``; 抖动按 ``base * (1 - jitter * u)`` 缩放, u 取自
        random_source 的 [0, 1). 于是:

        - ``jitter=0``  → 等待恰为 base (纯指数, 确定性)
        - ``jitter=1``  → 等待均匀落在 (0, base] (full jitter: 大批请求散开,
          但**永不越过 base**) —— 抖动只用来打散, 不把等待拉长

        Args:
            attempt: 第几次尝试失败 (1-based).

        Returns:
            float: 下一次尝试前应等待的秒数.
        """
        base = min(
            self.initial_delay * self.multiplier ** (attempt - 1), self.max_delay
        )
        if self.jitter <= 0:
            return base
        return base * (1.0 - self.jitter * self.random_source())

    def next_delay(self, *, attempt: int, elapsed: float) -> float | None:
        """第 ``attempt`` 次尝试失败后: 还能再试吗? 能则返回等待秒数 (#13).

        三个上限共同裁决 (ticket 第 2 条「上限可控」):

        1. 尝试次数: ``attempt >= max_attempts`` -> 不再试 (None)
        2. 总耗时: ``elapsed + delay > max_elapsed_seconds`` -> 不再试 (None)
           —— 等待只是白等, 等完还是要超预算. 边界按「恰好等于仍可挤下」
           处理 (预判语义, 与 LoopGuard 事后判定的「等于即触发」相反)
        3. 单次封顶: 由 backoff 的 max_delay 保证, 此处不重复判定

        Args:
            attempt: 刚失败的是第几次尝试 (1-based).
            elapsed: 自首次尝试起已耗时 (秒, 含调用耗时与已发生的等待).

        Returns:
            float | None: 下一次尝试前等待的秒数; None 表示放弃 —— 调用方
            该抛出 (异常路径) 或返回最后一次结果 (响应不合格路径).
        """
        if attempt >= self.max_attempts:
            return None
        delay = self.backoff(attempt)
        if (
            self.max_elapsed_seconds is not None
            and elapsed + delay > self.max_elapsed_seconds
        ):
            return None
        return delay
