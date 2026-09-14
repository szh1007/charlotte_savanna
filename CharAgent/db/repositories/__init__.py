"""仓储包: 四张表的取数口 (快照那张表由 checkpoint 包自己管, 见下).

一句话理解: 上层 (P1 的 server) 想读写业务数据, 只跟这个包的四个柜员打交道,
不写 SQL、不管事务、不碰表名.

| 仓储 | 管什么 | 典型问法 |
|------|--------|---------|
| `ThreadsRepository` | 会话 | 「这个用户最近聊了哪些」 |
| `RunsRepository` | 运行 (含状态机) | 「这次跑完了吗 / 把它取消」 |
| `MessagesRepository` | 消息 (**分可见与全量两种读法**) | 「给我这段对话的历史」 |
| `ToolCallsRepository` | 工具调用 | 「这次运行调了哪些工具」 |

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
from CharAgent.db.repositories.messages import MessagesRepository
from CharAgent.db.repositories.runs import RunsRepository
from CharAgent.db.repositories.threads import ThreadsRepository
from CharAgent.db.repositories.tool_calls import (
    ToolCallsRepository,
    build_tool_call,
)

__all__ = [
    "Database",
    "MessagesRepository",
    "PgRepository",
    "RunsRepository",
    "ThreadsRepository",
    "ToolCallsRepository",
    "build_tool_call",
]
