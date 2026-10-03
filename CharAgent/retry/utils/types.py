"""retry 包静态零件: 失败尝试记录 / 幂等认领裁决 / 熔断态与切换记录 (#13 #14 #17).

行为在包顶层 (policy 退避策略 / executor 驱动器 / chat_model 重试包装 /
circuit 熔断闸 / failover 主备包装 / idempotency 幂等键与存储), 纯数据结构与
回调形状集中于此供各模块与门面共享 (对齐 stream/utils, agent/utils 的
「表与行为分离」惯例).

大白话版 (本文件 = 三张单据的格式表):
- RetryAttempt: 「我又失败了一次」的记录单 —— 第几次、等多久再试、已经花掉
  多久、为什么失败; 递给 on_retry 的接收方 (记 token 账 / 观测打点).
  注意它是**失败尝试**的记录, 最后一次成功不产生记录.
- ClaimResult: 幂等认领的裁决书 —— 这次请求拿到执行权了吗? 还是有人正在做
  (IN_PROGRESS, 不得重复执行) / 早就做完了 (COMPLETED, 附上既有结果).
- CircuitState / ModelSwitch: 熔断闸现在是什么态, 以及「这一跳切到了谁」——
  后者递给 on_switch 的接收方 (日志 / 终端提示 / 观测).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum


class ClaimStatus(StrEnum):
    """幂等认领状态 (#17): 决定调用方「执行动作 / 等待 / 直接返回」."""

    CLAIMED = "claimed"  # 首次认领成功, 调用方应执行真实动作
    IN_PROGRESS = "in_progress"  # 已有在途执行 (并发重复请求), 不得重复执行
    COMPLETED = "completed"  # 已完成, 结果在 ClaimResult.result


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """claim() 的裁决结果 (幂等键查询的返回值).

    attributes:
        status: 认领状态 (ClaimStatus).
        result: status=COMPLETED 时的既有结果; 其余状态为 None.
    """

    status: ClaimStatus
    result: object | None = None

    @property
    def claimed(self) -> bool:
        """本次请求是否拿到执行权 —— 只有 CLAIMED 才应执行真实动作 (#17)."""
        return self.status is ClaimStatus.CLAIMED


@dataclass(slots=True)
class RetryAttempt:
    """一次「失败尝试」的记录 (on_retry 载荷, #13).

    attributes:
        attempt: 第几次尝试 (1-based) —— 即刚刚失败的那一次.
        delay: 本次失败后等待的秒数, 之后发起第 attempt+1 次.
        elapsed: 自首次尝试起已耗时 (秒), 供调用方对照自己的预算.
        reason: 失败原因简述 (异常类型 + 消息, 或不合格响应的说明).
        error: 异常路径的原始异常; 响应不合格路径为 None.
        result: 响应不合格路径的原样返回值 (模型场景可读其 usage —— 那一次
            已经计费, #13 的「重试重复烧 token」正是在此暴露); 异常路径为 None.
    """

    attempt: int
    delay: float
    elapsed: float
    reason: str
    error: BaseException | None = None
    result: object | None = None


class CircuitState(StrEnum):
    """熔断闸的三种态 (difficulties #14): 状态由「计数 + 时钟」算出来, 不由人设置."""

    CLOSED = "closed"  # 关闭: 正常放行, 数连续失败
    OPEN = "open"  # 打开: 直接拒绝, 一个请求都不发 (冷却中)
    HALF_OPEN = "half_open"  # 半开: 冷却已过, 只放**一个**探测


@dataclass(frozen=True, slots=True)
class ModelSwitch:
    """一次「熔断改走备份」的记录 (on_switch 载荷, #14).

    attributes:
        from_name: 从哪个模型切走 (闸刚被拨开的那个).
        to_name: 切到哪个模型 (这一跳实际去调的那个).
        reason: 为什么切 —— 一句人看的话 (连续几次失败熔断 / 探测失败等).
    """

    from_name: str
    to_name: str
    reason: str


# 重试通知回调: 每次「决定再试一次」时调用 (在等待之前), 同步或异步都接受.
# on_retry 收的是**序列** (可挂多个, 按序列顺序串行调用); 某个回调抛异常即
# 中止其后的回调并把异常向上抛 (对齐 stream 包的 EventSink: 这是调用方自己的
# 观测链, 通知失败要让它看见; 与 hooks 注册表的插件异常隔离语义相反).
type RetryCallback = Callable[[RetryAttempt], Awaitable[None] | None]

# 切换通知回调: 每次「这一跳改走另一个模型」时调用, 同步或异步都接受.
# 语义与 on_retry 完全一致 (序列 / 串行 / 异常不隔离), 只是载荷换成 ModelSwitch.
type SwitchCallback = Callable[[ModelSwitch], Awaitable[None] | None]
