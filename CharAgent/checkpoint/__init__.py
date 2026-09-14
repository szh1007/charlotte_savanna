"""checkpoint 包: 快照的序列化协议 + 三种存储实现 + 断点续跑 / time-travel 底座.

一句话理解: agent 跑一半断了 (进程挂了 / 等人工审批), 下次能不能接着跑、会不会
把已经做过的事重做一遍 —— 这一包就是回答这个问题的. 它规定「进度怎么变成能存
下来的文本」(序列化协议), 「存到哪儿」(三种存储实现), 以及「怎么从存档回到那
一刻」(agent/loop.py 的 resume).

设计依据 (CharAgent/docs):
- difficulties #5: 每 Turn 结束落快照; thread 分区; 恢复要「不重复已完成动作」;
  三种存储语义不同; JSON 主格式 + 自定义编码器 + schema 版本号向前兼容
- ADR-0002: Redis (KV 快照 + TTL, 快而弱一致) 与 Postgres (强一致 + 历史) 双实现,
  运行时配置切换; 内存版供测试
- 02-data-model.md §1/§3: Checkpoint 字段表与存储分布 (快照 -> Redis, 历史 -> PG)

结构总览 (顶层是行为模块, 静态零件收在 utils/):
- base.py          CheckpointSaver 协议 (插座形状: 存 / 取最新 / 按编号取 / 翻历史)
- memory.py        InMemoryCheckpointSaver: 记在草稿纸上, 重启就没 (测试与对照标尺)
- redis.py         RedisCheckpointSaver: 一个会话一个键 + TTL, 只留最新一帧
- postgres.py      PostgresCheckpointSaver: 一帧一行, 全历史, 能回溯
- serialization.py CheckpointCodec: JSON 编码 (datetime 之类打「行李牌」) + 版本迁移
- config.py        checkpoint_saver_from_env / build_saver: 配置切后端
- utils/           types (数据形状) / errors (异常族) / migrations (版本翻译)
                   / pending (找出还欠结果的工具调用) / fields (取值校验) / ddl (建表)

怎么用 (最小例子)::

    saver = checkpoint_saver_from_env()             # 配置说用哪个就用哪个
    loop = AgentLoop(model, tools, saver=saver, thread_id="t-1")
    result = await loop.run([{"role": "user", "content": "订单到哪了"}])
    # 断点续跑 / time-travel: 取一帧快照当起点接着跑
    checkpoint = await saver.load_latest("t-1")
    result = await loop.resume(checkpoint)

两种「换存储」的姿势 (都只改一行, loop 不动):
- 环境变量: CHECKPOINT_BACKEND=postgres (配 checkpoint_saver_from_env 用)
- 直接构造: PostgresCheckpointSaver(dsn=...)
"""

from __future__ import annotations

from CharAgent.checkpoint.base import CheckpointSaver
from CharAgent.checkpoint.config import (
    build_saver,
    checkpoint_saver_from_env,
    postgres_dsn,
)
from CharAgent.checkpoint.memory import InMemoryCheckpointSaver
from CharAgent.checkpoint.postgres import PostgresCheckpointSaver
from CharAgent.checkpoint.redis import RedisCheckpointSaver
from CharAgent.checkpoint.serialization import DEFAULT_CODEC, CheckpointCodec
from CharAgent.checkpoint.utils.errors import (
    CheckpointCapabilityError,
    CheckpointConfigError,
    CheckpointError,
    CheckpointMigrationError,
    CheckpointSerializationError,
    CheckpointStorageError,
)
from CharAgent.checkpoint.utils.history import format_history, summarize
from CharAgent.checkpoint.utils.migrations import migrate_body
from CharAgent.checkpoint.utils.pending import pending_tool_calls
from CharAgent.checkpoint.utils.types import (
    SCHEMA_VERSION,
    Checkpoint,
    CheckpointCapabilities,
    CheckpointMetadata,
    CheckpointSource,
    CheckpointState,
    Suspension,
    check_identifier,
)

__all__ = [
    "DEFAULT_CODEC",
    "SCHEMA_VERSION",
    "Checkpoint",
    "CheckpointCapabilities",
    "CheckpointCapabilityError",
    "CheckpointCodec",
    "CheckpointConfigError",
    "CheckpointError",
    "CheckpointMetadata",
    "CheckpointMigrationError",
    "CheckpointSaver",
    "CheckpointSerializationError",
    "CheckpointSource",
    "CheckpointState",
    "CheckpointStorageError",
    "InMemoryCheckpointSaver",
    "PostgresCheckpointSaver",
    "RedisCheckpointSaver",
    "Suspension",
    "build_saver",
    "check_identifier",
    "checkpoint_saver_from_env",
    "format_history",
    "migrate_body",
    "pending_tool_calls",
    "postgres_dsn",
    "summarize",
]
