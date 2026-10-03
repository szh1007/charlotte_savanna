"""retry 包: 重试 + 指数退避 + jitter + 熔断 + failover + 幂等键 (#13 #14 #17).

一句话理解: 模型调用偶尔会因为「不是我们的错」的原因失败 (上游限流 429、
服务端 5xx、网络超时), 这种失败重试一次往往就好了; 而参数错、模型名错这类
4xx 重试一百次也没用. 本包就是那条**判断 + 等待 + 再来一次**的流水线; 连续错
几次还不好, 说明这家真的挂了 —— 那就**熔断** (一段时间一个请求都不发) 并切到
备份模型 (**failover**). 外加一张防重复的「幂等键」凭证 (重试不能把同一笔真实
操作做两遍).

设计依据 (CharAgent/docs):
- difficulties #13: 只对瞬态错误重试 (429 / 5xx / 超时), 4xx 直接放弃;
  指数退避 + jitter 散开惊群; 重试会重复计费 token, 必须可挂钩 (不给假账)
- difficulties #14: 三态熔断 (关闭 / 打开 / 半开) + 切备份模型; 打开期间
  **真的不发请求**, 半开**只放一个**探测; 切到备份后按实际服务的模型记账
- ChatModel 是薄协议 SPI —— 重试与熔断都以**组合**方式包装模型, 不改协议
  不改 loop (「只加不改」)

结构总览 (顶层 = 行为模块, 静态零件在 utils/):
- policy.py        RetryPolicy: 退避序列 (指数 + jitter) / 三个上限的裁决
                   (尝试次数 / 总耗时 / 单次退避封顶) / 瞬态判据 is_retryable
- executor.py      retry_async: 通用重试驱动器 (异常路径 + 响应不合格路径)
- chat_model.py    RetryingChatModel: ChatModel 协议的重试包装 (模型侧接线)
- circuit.py       CircuitBreaker + CircuitPolicy: 三态熔断闸 (只放/不放)
                   与它的规矩本 (阈值 / 冷却 / 两条注入缝)
- failover.py      FailoverChatModel: 主备包装 (两个闸 + 主熔断就走备)
- serving.py       ServingRecord: 运行级服务台账 (这一趟是谁在服务, 记账归因用)
- idempotency.py   IdempotencyKey (生成 + 校验) + IdempotencyStore 协议
                   + InMemoryIdempotencyStore (进程内实现)
- utils/           支撑子包: types (RetryAttempt / ClaimResult / CircuitState /
                   ModelSwitch / 两个回调形状) / errors (RetryConfigError /
                   IdempotencyKeyError / CircuitOpenError)

三层包装的嵌套顺序 (ADR-0025 定的, 换一下就是行为差异极大的改动)::

    RetryingChatModel(FailoverChatModel(主, 备))     <- 熔断闸看到每一次物理调用
    ChatSession(model=..., ...)                       <- 上层只当普通 ChatModel 用

三条缝 (对齐 LoopGuard.time_source 的注入惯例, 保证测试零等待且确定, #61):
`sleep` (不必真等) · `time_source` (固定时钟, 重试与熔断共用一套惯例) ·
`random_source` (固定抖动).

与 loop 的分工: **重试与熔断都归本包** —— AgentLoop 只如实上报「上游中断 / 模型
调用失败」, 是否再试一次 / 换一家由调用方决定 (loop docstring 已声明该边界).
典型接线: ``AgentLoop(model=RetryingChatModel(FailoverChatModel(主, 备)), ...)``.
"""

from __future__ import annotations

from CharAgent.retry.chat_model import RetryingChatModel
from CharAgent.retry.circuit import CircuitBreaker, CircuitPolicy
from CharAgent.retry.executor import retry_async
from CharAgent.retry.failover import FailoverChatModel
from CharAgent.retry.idempotency import (
    IdempotencyKey,
    IdempotencyStore,
    InMemoryIdempotencyStore,
)
from CharAgent.retry.policy import RetryPolicy, is_retryable
from CharAgent.retry.serving import ServingRecord, current_serving, serving_scope
from CharAgent.retry.utils.errors import (
    CircuitOpenError,
    IdempotencyKeyError,
    RetryConfigError,
    RetryError,
)
from CharAgent.retry.utils.types import (
    CircuitState,
    ClaimResult,
    ClaimStatus,
    ModelSwitch,
    RetryAttempt,
    RetryCallback,
    SwitchCallback,
)

__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitPolicy",
    "CircuitState",
    "ClaimResult",
    "ClaimStatus",
    "FailoverChatModel",
    "IdempotencyKey",
    "IdempotencyKeyError",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "ModelSwitch",
    "RetryAttempt",
    "RetryCallback",
    "RetryConfigError",
    "RetryError",
    "RetryPolicy",
    "RetryingChatModel",
    "ServingRecord",
    "SwitchCallback",
    "current_serving",
    "is_retryable",
    "retry_async",
    "serving_scope",
]
