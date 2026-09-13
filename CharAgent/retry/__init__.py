"""retry 包: 重试 + 指数退避 + jitter + 幂等键 (issue 06 / difficulties #13).

一句话理解: 模型调用偶尔会因为「不是我们的错」的原因失败 (上游限流 429、
服务端 5xx、网络超时), 这种失败重试一次往往就好了; 而参数错、模型名错这类
4xx 重试一百次也没用. 本包就是那条**判断 + 等待 + 再来一次**的流水线, 外加
一张防重复的「幂等键」凭证 (重试不能把同一笔真实操作做两遍).

设计依据 (CharAgent/docs):
- difficulties #13: 只对瞬态错误重试 (429 / 5xx / 超时), 4xx 直接放弃;
  指数退避 + jitter 散开惊群; 重试会重复计费 token, 必须可挂钩 (不给假账)
- CONTEXT.md 词条: RetryPolicy / IdempotencyKey
- ADR-0001: ChatModel 是薄协议 SPI —— 重试以**组合**方式包装模型, 不改协议
  不改 loop (issue 05 的「只加不改」同一思路)

结构总览 (顶层 = 行为模块, 静态零件在 utils/):
- policy.py        RetryPolicy: 退避序列 (指数 + jitter) / 三个上限的裁决
                   (尝试次数 / 总耗时 / 单次退避封顶) / 瞬态判据 is_retryable
- executor.py      retry_async: 通用重试驱动器 (异常路径 + 响应不合格路径)
- chat_model.py    RetryingChatModel: ChatModel 协议的重试包装 (模型侧接线)
- idempotency.py   IdempotencyKey (生成 + 校验) + IdempotencyStore 协议
                   + InMemoryIdempotencyStore (进程内实现)
- utils/           支撑子包: types (RetryAttempt / ClaimResult / RetryCallback)
                   / errors (RetryConfigError / IdempotencyKeyError)

三条缝 (对齐 LoopGuard.time_source 的注入惯例, 保证测试零等待且确定, #61):
`sleep` (不必真等) · `time_source` (固定时钟) · `random_source` (固定抖动).

与 loop 的分工: **重试归本包** —— AgentLoop 只如实上报「上游中断 / 模型调用
失败」, 是否再试一次由调用方决定 (loop docstring 已声明该边界). 典型接线:
``AgentLoop(model=RetryingChatModel(chat_model_from_env()), ...)``.
"""

from __future__ import annotations

from CharAgent.retry.chat_model import RetryingChatModel
from CharAgent.retry.executor import retry_async
from CharAgent.retry.idempotency import (
    IdempotencyKey,
    IdempotencyStore,
    InMemoryIdempotencyStore,
)
from CharAgent.retry.policy import RetryPolicy, is_retryable
from CharAgent.retry.utils.errors import (
    IdempotencyKeyError,
    RetryConfigError,
    RetryError,
)
from CharAgent.retry.utils.types import ClaimResult, ClaimStatus, RetryAttempt

__all__ = [
    "ClaimResult",
    "ClaimStatus",
    "IdempotencyKey",
    "IdempotencyKeyError",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "RetryAttempt",
    "RetryConfigError",
    "RetryError",
    "RetryPolicy",
    "RetryingChatModel",
    "is_retryable",
    "retry_async",
]
