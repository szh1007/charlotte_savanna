"""节点内的并发: 带上限地跑一批协程 (C18).

三路召回节点各要按关键词发 N 次检索, 此前是 `for` 里逐个 `await` ——
N 个关键词就是 N 个 RTT 相加. 这里给一个公共件, 三个节点共用.

**为什么必须有上限**: 关键词个数由 LLM 扩展决定 (实测 2~10 个), 无上限地全放
出去, 一旦某题扩出几十个词就会把嵌入服务 / Qdrant / ES 的连接池打满 ——
那是把"慢"换成"不稳定". 上限值见 `app_config.recall.concurrency`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable


async def gather_limited[T](coros: Iterable[Awaitable[T]], limit: int) -> list[T]:
    """并发跑一批协程, 同时在飞的不超过 `limit` 个; 结果按入参顺序返回.

    Args:
        coros: 待执行的协程 (尚未 await, 传进来不会立刻开跑)
        limit: 并发上限, 小于 1 时按 1 处理 —— `Semaphore(0)` 会把整条链挂死

    Returns:
        与入参**同序**的结果列表. 顺序是契约而不是巧合: 下游按"先到先得"去重
        (同一个字段被多个关键词召回时留第一个), 顺序一变, 留下的那一份就换了.
    """
    semaphore = asyncio.Semaphore(max(1, limit))

    async def guarded(coro: Awaitable[T]) -> T:
        async with semaphore:
            return await coro

    return await asyncio.gather(*(guarded(coro) for coro in coros))
