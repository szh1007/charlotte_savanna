"""db 包: 五实体的数据模型 + 表定义 + 仓储 (difficulties #12).

目录叫 `db/` 而不是 `models/`: 与 `CharAgent/model/` (LLM 模型层) 名字太近, 两个
都叫 model 会分不清谁是谁. 这里的东西全是数据库相关的, `db` 一眼到位.

一句话理解: 这是**数据库这一层的全部家当** —— 表长什么样、实体是什么、状态能
怎么变、数据怎么读写. 上层 (P1 的 server) 只跟它打交道, 不写 SQL.

它解决什么问题: agent 跑一次会产生很多**要留下来的东西** —— 谁开的会话、这次
运行跑成什么样、用户问了什么、模型答了什么、调了哪些工具. P0 的 checkpoint 只
解决「接着跑」, 不解决「查得到、看得到」. 本包补的就是后者.

九个部分的分工 (行为在上, 静态零件在下):

| 文件 | 管什么 |
|------|--------|
| `schema.py` | 表定义唯一来源 (迁移与快照存储都从这里取) |
| `entities.py` | 五个实体 + 四个状态枚举 |
| `state.py` | 运行状态机的规则 (合法迁移表 + 结束原因映射) |
| `conversation.py` | 会话消息分层 (哪几条给前端看; 答复取 `LoopResult.content`) |
| `database.py` | 连库与事务 (`PgDatabase`: 同步引擎 + `asyncio.to_thread`) |
| `config.py` | 连接配置 (环境变量 → 连接串) |
| `cost.py` | 成本折算: 收尾那一刻按当时价目表算好金额 (写进 runs, 事后不重算) |
| `errors.py` | 三类错误 (配置错 / 状态迁移非法 / 库出错) |
| `repositories/` | 五个取数口 (会话 / 运行 / 消息 / 工具调用 / 幂等登记) |
| `recorder.py` | 会话记录: 一次运行收尾把这一轮写进上面几张表 |
| | (本包**第一个生产调用方** —— 此前那几张表没有任何生产代码在用) |

怎么用 (典型的一段: 跑完一次运行, 把结果落库)::

    db = PgDatabase()                       # 从环境变量读连接串
    threads = ThreadsRepository(db)
    runs = RunsRepository(db)
    messages = MessagesRepository(db)

    thread = await threads.add(tenant_id="t-1", user_id="u-1")
    run = await runs.add(thread_id=thread.thread_id, request_id="req-1")

    # ... 跑 agent loop, 拿到 result ...

    await runs.try_transition(
        run.run_id, RunStatus.RUNNING, run_status_for_outcome(result.outcome)
    )
    for turn in conversation_turns(result.messages, result.content, since=prior_len):
        await messages.add_turn(
            thread_id=thread.thread_id, turn=turn, run_id=run.run_id
        )

    # 前端要的会话历史: 只有一问一答, 没有内部件
    history = await messages.list_conversation(thread.thread_id)

幂等登记簿走的是另一条路 (它不挂在会话下面 —— 键由调用方拼出来): 一个真实动作
执行**前**先 `PgIdempotencyStore.claim(key)`, 拿到执行权才做, 做完 `complete(key,
result)`; 重复请求会拿到「有人在做」或既有结果, 于是同一件事只发生一次 (细节见
`repositories/idempotency.py` 的模块 docstring).

**快照 (checkpoint) 的读写不在这里**: 那个有断点续跑 / 翻历史 / time-travel 的
一整套语义, 归 `checkpoint/postgres.py` 的 `PostgresCheckpointSaver`. 本包只提供
那张表的定义 (`CheckpointRow` 与 `schema.checkpoints`), 让迁移体系与 ORM 有一套
完整口径 —— 不另造一个功能重叠的入口.

门面里**没有** `config.py` 的两个环境变量名常量 (`CHARAGENT_DB_DSN` /
`CHARAGENT_DB_ECHO`): 它们是配置模块的内部零件 (需要的人 `from
CharAgent.db.config import ...` 即可取), 上浮到门面只会变成两个看不出归属的
通名 —— 与 `checkpoint` 门面 (同样不导出它的 7 个 `ENV_*`) 保持一致口径.
"""

from __future__ import annotations

from CharAgent.db.config import (
    echo_enabled,
    sqlalchemy_url,
)
from CharAgent.db.conversation import (
    TranscriptLine,
    TurnPair,
    assistant_answer,
    conversation_turns,
    count_visible,
    has_visible_answer,
    recorded_transcript,
    visible_transcript,
)
from CharAgent.db.cost import (
    CostGap,
    CostLine,
    PeakRule,
    PriceTable,
    RunCost,
    TierPrices,
    TierVerdict,
    cost_of,
    ensure_pricing_ready,
    load_pricing,
)
from CharAgent.db.database import PgDatabase
from CharAgent.db.entities import (
    CheckpointRow,
    Message,
    MessageRole,
    Run,
    RunStatus,
    Thread,
    ThreadStatus,
    ToolCall,
    ToolCallStatus,
)
from CharAgent.db.errors import (
    DataConfigError,
    DataStoreError,
    DbError,
    InvalidTransitionError,
    PricingNotReadyError,
)
from CharAgent.db.recorder import ConversationRecorder, RunRecorder, title_for
from CharAgent.db.repositories import (
    Database,
    MessagesRepository,
    PgIdempotencyStore,
    PgRepository,
    RunsRepository,
    ThreadsRepository,
    ToolCallsRepository,
    build_tool_call,
    message_id_for,
)
from CharAgent.db.repositories.utils.mapping import model_to_dict
from CharAgent.db.schema import (
    ALL_TABLES,
    TABLE_NAMES,
    checkpoints,
    messages,
    metadata,
    runs,
    threads,
    tool_calls,
)
from CharAgent.db.state import (
    ALLOWED_TRANSITIONS,
    RUN_STATUS_FOR_OUTCOME,
    SETTLEABLE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    can_transition,
    ensure_transition,
    run_status_for_outcome,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ALL_TABLES",
    "RUN_STATUS_FOR_OUTCOME",
    "SETTLEABLE_RUN_STATUSES",
    "TABLE_NAMES",
    "TERMINAL_RUN_STATUSES",
    "CheckpointRow",
    "ConversationRecorder",
    "CostGap",
    "CostLine",
    "DataConfigError",
    "DataStoreError",
    "Database",
    "DbError",
    "InvalidTransitionError",
    "Message",
    "MessageRole",
    "MessagesRepository",
    "PeakRule",
    "PgDatabase",
    "PgIdempotencyStore",
    "PgRepository",
    "PriceTable",
    "PricingNotReadyError",
    "Run",
    "RunCost",
    "RunRecorder",
    "RunStatus",
    "RunsRepository",
    "Thread",
    "ThreadStatus",
    "ThreadsRepository",
    "TierPrices",
    "TierVerdict",
    "ToolCall",
    "ToolCallStatus",
    "ToolCallsRepository",
    "TranscriptLine",
    "TurnPair",
    "assistant_answer",
    "build_tool_call",
    "can_transition",
    "checkpoints",
    "conversation_turns",
    "cost_of",
    "count_visible",
    "echo_enabled",
    "ensure_pricing_ready",
    "ensure_transition",
    "has_visible_answer",
    "load_pricing",
    "message_id_for",
    "messages",
    "metadata",
    "model_to_dict",
    "recorded_transcript",
    "run_status_for_outcome",
    "runs",
    "sqlalchemy_url",
    "threads",
    "title_for",
    "tool_calls",
    "visible_transcript",
]
