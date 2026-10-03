"""CircuitBreaker (difficulties #14): 三态熔断闸 —— 「这家现在别打了」.

一句话理解: 重试是「再试一次」, 熔断是「暂时一次都别再试了」. 上游连续挂掉几次
之后, 继续打过去只会得到同样的失败 (还白占着调用方的等待时间), 于是把闸拉下来:
打开期间**一个请求都不发出去**, 冷却一会儿再放**一个**探子过去看人家缓过来没有.

三态 (状态不由人设置, 由「计数 + 时钟」现算):

| 态 | 什么时候 | 行为 |
|---|---|---|
| 关闭 | 默认 | 正常放行, 数**连续**失败 |
| 打开 | 连续失败到阈值 | 直接拒绝 (抛 CircuitOpenError), 一个请求都不发 |
| 半开 | 冷却时间到 | 只放行**一个**探测; 成功 -> 关闭, 失败 -> 回到打开 |

三个容易做错的点 (都在下面钉着):
1. 打开期间**真的不发请求** —— 否则「熔断」只是把错误换了个说法, 下游照样被打;
2. 半开只放行一个 —— 否则冷却一到, 积压的请求一起涌出去 (惊群), 把刚缓过来的
   下游再打挂;
3. 冷却与阈值可配 + 时间源可注入 —— 测试零真实等待 (#61).

只数「这个模型自己欠的账」: 判据默认取 retry 包的 `is_retryable` (429 / 5xx /
连接失败 / 超时), 参数错这类**不算** —— 换个模型发同样的请求一样会错, 为它熔断
毫无意义 (那种失败原样上抛, 由调用方按自己的语义处理).

**成功的调用把连续失败清零** (「连续」二字落在实处): 一次成功说明这条路上一次
是通的, 从那一刻重新数. 于是偶发抖动不会攒成一次误熔断.

**迟到的结论一律不参与** (并发调用才会遇到): 一次调用发出去之后, 别的调用可能已经
把闸拨开了 —— 它在**上一次打开之前**走出去, 却在那之后才回来. 那种失败的账不能
记, 理由不是记账洁癖: 它会再跳一次闸, 把冷却期**从头再数** (刚被前一批失败拨开的
闸, 因为一个更早的失败多关一会儿); 同理, 迟到的成功也不能拿来合闸 (一个旧结论
推翻一串新证据). 判据是「闸不关着、而这次调用又不是那个探测」—— 半开探测位只有
一个, 所以剩下的只可能是「跳闸之前发出去的那一次」.

并发: 框架是单事件循环的 asyncio, 而本类的三个动作 (acquire / record_*) 都是
**同步**的 —— 中间没有 await, 于是「看状态 + 占探测位」是一次原子操作, 不会出现
两个调用同时拿到同一个探测位 (上面那条「迟到」讲的正是这种并发下的账目, 不是
竞态). 跨进程共享闸状态 (多实例部署) 不在本类范围 (#21).

大白话版: 一个带记忆的开关. 它记着「连续错了几次」: 错到阈值就跳闸, 跳闸期间
谁也别想再发请求 (直接报「这条路现在不通」); 冷却到点放一个人去探路, 探通了就
合闸, 探不通就再等一轮.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from CharAgent.retry.policy import is_retryable
from CharAgent.retry.utils.errors import CircuitOpenError, RetryConfigError
from CharAgent.retry.utils.types import CircuitState
from CharAgent.structured_logging import get_logger

logger = get_logger("retry")


@dataclass(frozen=True, slots=True)
class CircuitPolicy:
    """熔断的规矩本: 阈值 / 冷却 + 两条注入缝 (difficulties #14).

    与 `RetryPolicy` 同一个形状 (那个装「重试的规矩」, 这个装「跳闸的规矩」): 这
    四样总是一起用, 打成一份传; 校验在构造期做完 (fail fast), 运行期只管用. 两个
    闸 (主 / 备) 共用同一份 —— 它说的是「什么才算这家的问题」, 与是哪一家无关.

    attributes:
        failure_threshold: 连续失败几次跳闸 (至少 1), 默认 3.
        cooldown_seconds: 跳闸后冷却多久才放探测 (> 0), 默认 30 秒.
        time_source: 计时器注入点 (默认 time.monotonic; 测试换固定时钟, 零真实等待).
        counts_as_failure: 哪些失败算这个模型的账 (默认 is_retryable: 429 / 5xx /
            连接失败 / 超时). 判为 False 的失败**不计数也不改状态**.

    Raises:
        RetryConfigError: 阈值 / 冷却取值非法.
    """

    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    time_source: Callable[[], float] = field(default=time.monotonic, repr=False)
    counts_as_failure: Callable[[BaseException], bool] = field(
        default=is_retryable, repr=False
    )

    def __post_init__(self) -> None:
        """配置校验: 非法数值构造期报错 (fail fast, 与 RetryPolicy 同一条规矩)."""
        if self.failure_threshold < 1:
            raise RetryConfigError(
                f"failure_threshold 必须 >= 1, 实际: {self.failure_threshold}"
            )
        if self.cooldown_seconds <= 0:
            raise RetryConfigError(
                f"cooldown_seconds 必须 > 0, 实际: {self.cooldown_seconds}"
            )


class CircuitBreaker:
    """一个模型的三态熔断闸 (difficulties #14).

    Args:
        name: 这个闸看着谁 (模型名 —— 进日志与拒发文案).
        policy: 跳闸的规矩 (阈值 / 冷却 / 两条注入缝); None 表示 `CircuitPolicy()`
            默认值 (连错 3 次跳闸, 冷却 30 秒).
    """

    def __init__(self, name: str, *, policy: CircuitPolicy | None = None) -> None:
        self._name = name
        self._policy = policy if policy is not None else CircuitPolicy()
        # 三样状态: 连续失败计数 / 跳闸时刻 (None = 开着) / 探测位是否被占
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def name(self) -> str:
        """这个闸看着哪个模型 (进日志与拒发文案)."""
        return self._name

    @property
    def policy(self) -> CircuitPolicy:
        """这个闸用的规矩本 (观测用: 阈值 / 冷却 / 两条缝都在上面)."""
        return self._policy

    @property
    def failures(self) -> int:
        """当前连续失败次数.

        跳闸后**停在阈值上** (不会越数越大): 打开期间一个请求都不发, 也就没有新的
        失败可数; 它回答的是「合闸要清零的那笔账是多少」.
        """
        return self._failures

    @property
    def state(self) -> CircuitState:
        """现在是什么态 —— 用注入的时钟现算, 不落一个定时器 (没有后台任务要收)."""
        if self._opened_at is None:
            return CircuitState.CLOSED
        if self._time_source() - self._opened_at >= self._policy.cooldown_seconds:
            # 冷却到点即半开: 「放不放探测」由 acquire 决定 —— 探测位只占一个
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def acquire(self) -> None:
        """要一个放行位; 不许过就抛 CircuitOpenError (打开态 / 探测位已占).

        关闭态直接放行 (不占任何位); 半开态**占住唯一的探测位**; 打开态直接拒.

        Raises:
            CircuitOpenError: 现在不该发这次请求 (不可重试, 见该异常类).
        """
        state = self.state
        if state is CircuitState.CLOSED:
            return
        if state is CircuitState.HALF_OPEN:
            if not self._probe_in_flight:
                # 占位与状态检查之间没有 await, 于是「只放一个」是原子的
                self._probe_in_flight = True
                return
            raise self._refusal("冷却已过, 但已有一个探测在飞")
        raise self._refusal(f"冷却还需 {self._remaining():.0f} 秒")

    def record_success(self) -> None:
        """这一次调用成功了: 合闸 + 连续失败清零.

        半开态的探测成功走的就是这条路 (探测位一并还回来); 跳闸之前发出去的那次
        调用迟到的成功**不动闸** (见模块 docstring 的「迟到的结论一律不参与」).
        """
        if self._is_stale():
            return
        if self._opened_at is not None:
            logger.info(
                "模型 %s 熔断解除 (闸合): 这一次调用成功 (此前连续失败 %d 次)",
                self._name,
                self._failures,
            )
        self._probe_in_flight = False
        self._failures = 0
        self._opened_at = None

    def record_failure(self, exc: BaseException) -> bool:
        """记一次失败; 返回值 = 「这一下把闸拨开了吗」(调用方据此立刻改走备份).

        判为**不算故障**的失败 (默认判据外的一切, 如 400 参数错) 直接返回 False:
        不计数、不改状态、不跳闸 —— 只把探测位还回来. 迟到的失败同样不算 (见模块
        docstring).

        Args:
            exc: 这次调用抛出的异常 (判据按它决定算不算故障).

        Returns:
            bool: True = 刚刚跳闸 (现在起一个请求都不发); False = 记账完毕但闸
            还开着门 (或这次失败压根不算账).
        """
        stale = self._is_stale()
        self._probe_in_flight = False
        if not self._policy.counts_as_failure(exc):
            return False
        if stale:
            return False
        if self.state is CircuitState.HALF_OPEN:
            # 探测失败: 回到打开, 冷却**从头再数** (说明人家还没缓过来)
            self._trip()
            return True
        self._failures += 1
        if self._failures >= self._policy.failure_threshold:
            self._trip()
            return True
        return False

    def record_abort(self) -> None:
        """这次调用没有结论 (被取消 / 放弃等待): 只把探测位还回来.

        为什么不记成失败: 取消是**调用方**的决定 (Ctrl-C / kill switch), 不是上游
        的病 —— 拿它去跳闸等于让一次用户打断把整条路熔断掉.

        为什么必须还: 半开态的探测位不还, 那个模型就再也放不出任何请求了 (闸卡在
        「探测在飞」上, 后续每一个调用都会被拒), 而它本来就要靠这一次探测活过来.
        """
        self._probe_in_flight = False

    def _is_stale(self) -> bool:
        """这次结论是不是「跳闸之前发出去的那次调用」的迟到回音 (见模块 docstring)."""
        return self._opened_at is not None and not self._probe_in_flight

    def _time_source(self) -> float:
        """现在的时刻 (注入的时钟)."""
        return self._policy.time_source()

    def _remaining(self) -> float:
        """冷却还剩几秒 (打开态才有意义)."""
        if self._opened_at is None:
            return 0.0
        elapsed = self._time_source() - self._opened_at
        return max(0.0, self._policy.cooldown_seconds - elapsed)

    def _refusal(self, detail: str) -> CircuitOpenError:
        """造一条「现在不许过」的拒词 (acquire 抛的就是它).

        Args:
            detail: 这次为什么不许过 (冷却还剩多久 / 探测位被占).

        Returns:
            CircuitOpenError: 带模型名与当前连续失败次数的一行事实.
        """
        return CircuitOpenError(
            f"模型 {self._name} 熔断中 (连续 {self._failures} 次失败): "
            f"{detail} —— 这次请求没有发出去",
            name=self._name,
        )

    def _trip(self) -> None:
        """跳闸: 记下这一刻 + 留一行日志 (开 / 关的痕迹落在日志与拒发文案上)."""
        self._failures = max(self._failures, self._policy.failure_threshold)
        self._opened_at = self._time_source()
        logger.warning(
            "模型 %s 熔断跳闸 (闸开): 连续 %d 次失败, "
            "冷却 %.0f 秒 (这段时间一次都不发)",
            self._name,
            self._failures,
            self._policy.cooldown_seconds,
        )
