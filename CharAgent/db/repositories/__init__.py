"""仓储包: 五张表的取数口 (快照那张表由 checkpoint 包自己管, 见下).

一句话理解: 上层 (P1 的 server) 想读写业务数据, 只跟这个包的五个柜员打交道,
不写 SQL、不管事务、不碰表名.

| 仓储 | 管什么 | 典型问法 |
|------|--------|---------|
| `ThreadsRepository` | 会话 | 「这个用户最近聊了哪些」 |
| `RunsRepository` | 运行 (含状态机) | 「这次跑完了吗 / 把它取消」 |
| `MessagesRepository` | 消息 (**分可见与全量两种读法**) | 「给我这段对话的历史」 |
| `ToolCallsRepository` | 工具调用 | 「这次运行调了哪些工具」 |
| `PgIdempotencyStore` | 幂等登记簿 | 「这个动作做过了吗 / 结果是什么」 |

> 最后那个柜员的形状与别人不同: 别的仓储是「按业务概念取数」的一般取数口, 而它
> **直接就是 `retry/` 定义的 `IdempotencyStore` 协议那一张脸** (claim / complete /
> release 三个动作, 没有第四条路可走) —— 这张表只有「认领」一件事, 协议已经把它
> 说全了, 再包一层业务形状只会多个没人读的中间层. 它归本包是因为**要用库**,
> 而 `retry/` 要保持零数据库依赖 (见那个文件的模块 docstring).

> 五实体里少了 **checkpoints** —— 那张表的柜员不在本包, 在
> `checkpoint/postgres.py` (它是 `CheckpointSaver` 协议的第三个实现, 与内存版 /
> Redis 版并列, 由配置切换). 理由见文件末尾.

**快照 (checkpoint) 那张表不在这里**: 它的读写归 `checkpoint/postgres.py`
(`PostgresCheckpointSaver` 实现的是 CheckpointSaver 协议, 有断点续跑 / 翻历史 /
time-travel 一整套语义). 本包只提供那张表的**定义** (schema.py), 不再造一个功能
重叠的柜员 —— 两个入口改同一张快照表, 迟早会出现「一边按协议的语义写, 一边按
普通表写」造成的怪数据.
"""

from __future__ import annotations

from CharAgent.db.repositories.base import Database, PgRepository
from CharAgent.db.repositories.idempotency import PgIdempotencyStore
from CharAgent.db.repositories.messages import MessagesRepository, message_id_for
from CharAgent.db.repositories.runs import RunsRepository
from CharAgent.db.repositories.threads import ThreadsRepository
from CharAgent.db.repositories.tool_calls import (
    ToolCallsRepository,
    build_tool_call,
)

__all__ = [
    "Database",
    "MessagesRepository",
    "PgIdempotencyStore",
    "PgRepository",
    "RunsRepository",
    "ThreadsRepository",
    "ToolCallsRepository",
    "build_tool_call",
    "message_id_for",
]
