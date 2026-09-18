"""CharAgent: 从零手写 AI Agent 运行时框架 (九个包, 公共 API 汇聚导出).

一句话理解: 本文件是**框架的门脸** —— 想用这个框架的人只 import 一次
`CharAgent`, 就能拿到全部对外公共 API, 不必记住「哪个东西在哪个子包里」.
各子包自己也各有一份门面 (`CharAgent.checkpoint.__all__` 等), 本文件是它们的
合集, 不新造 API.

汇聚的九个包 (P0 的八个 + 后来补的 `prompt`; 职责见下表):

| 包 | 一句话 |
|----|--------|
| `model` | 薄 ChatModel 协议 + httpx 裸调 / openai SDK 双适配器 |
| `tool` | @tool 装饰器 + JSON schema 自动生成 + 可操作错误语义 |
| `agent` | 手写 agent loop (并行工具 / 错误自纠错 / 循环防护 / 截断处理) |
| `stream` | 流式事件总线 (六类事件 + seq + 四条状态机不变量) |
| `hooks` | hook 注册表骨架 (五个触发点, 空注册零开销) |
| `retry` | 重试退避 + 幂等键 (包在 ChatModel 协议层, loop 零改动) |
| `checkpoint` | 快照序列化协议 + 内存 / Redis / Postgres 三实现 + 断点续跑 |
| `db` | 五实体数据模型 + 表定义 + alembic 迁移 + 仓储 |
| `prompt` | 提示词集中存放与按名加载 (`templates/*.prompt` + `load_prompt`) |

两条刻意的排除 (不是漏了):

1. **`tool` (小写, 那个 @tool 装饰器) 不在顶层导出** —— 它与子包
   `CharAgent.tool` 同名, 导出会遮蔽包属性, 于是 `from CharAgent.tool import
   tool` 成了唯一写法. 其余名字没有这个冲突.
2. **`client` 不入本文件** —— 它是**应用入口** (`python -m CharAgent.client`), 不是
   库 API: 没有人会 `from CharAgent import main`. 想跑演示直接敲那条命令.

为什么现在才汇聚 (历史): 各包的门面一直是齐的, 根门面却长期只导出了
model + tool —— 各处都记着「agent / stream / hooks / retry / checkpoint / db
尚未顶层导出, 同批处理」, 几轮都往后推, 一直没做. 收尾时 (P0 验收线) 把它一次
补完, 并加了一条防漂移用例 (`tests/test_root_facade.py`: 子包 __all__ 里的每个
名字, 根门面都必须有).
"""

from __future__ import annotations

# --- agent: 手写 agent loop + 循环防护 ------------------------------------
from CharAgent.agent import (
    AgentLoop,
    GuardConfigError,
    LoopConfigError,
    LoopGuard,
    LoopOutcome,
    LoopResult,
    RunContext,
    ToolProvider,
    TruncationStrategy,
    TurnRecord,
)

# --- checkpoint: 快照协议 + 三实现 + 断点续跑 ------------------------------
from CharAgent.checkpoint import (
    BACKEND_NAMES,
    DEFAULT_CODEC,
    SCHEMA_VERSION,
    Checkpoint,
    CheckpointCapabilities,
    CheckpointCapabilityError,
    CheckpointCodec,
    CheckpointConfigError,
    CheckpointError,
    CheckpointMetadata,
    CheckpointMigrationError,
    CheckpointSaver,
    CheckpointSerializationError,
    CheckpointSource,
    CheckpointState,
    CheckpointStorageError,
    InMemoryCheckpointSaver,
    PostgresCheckpointSaver,
    RedisCheckpointSaver,
    Suspension,
    build_saver,
    check_identifier,
    checkpoint_saver_from_env,
    format_history,
    migrate_body,
    pending_tool_calls,
    postgres_dsn,
    summarize,
)

# --- db: 五实体数据模型 + 表定义 + 仓储 ------------------------------------
from CharAgent.db import (
    ALL_TABLES,
    ALLOWED_TRANSITIONS,
    RUN_STATUS_FOR_OUTCOME,
    TABLE_NAMES,
    TERMINAL_RUN_STATUSES,
    CheckpointRow,
    Database,
    DataConfigError,
    DataStoreError,
    DbError,
    InvalidTransitionError,
    Message,
    MessageRole,
    MessagesRepository,
    PgDatabase,
    PgRepository,
    Run,
    RunsRepository,
    RunStatus,
    Thread,
    ThreadsRepository,
    ThreadStatus,
    ToolCall,
    ToolCallsRepository,
    ToolCallStatus,
    TranscriptLine,
    TurnPair,
    assistant_answer,
    build_tool_call,
    can_transition,
    checkpoints,
    conversation_turns,
    count_visible,
    echo_enabled,
    ensure_transition,
    messages,
    metadata,
    model_to_dict,
    run_status_for_outcome,
    runs,
    sqlalchemy_url,
    threads,
    tool_calls,
    visible_transcript,
)

# --- hooks: hook 注册表骨架 (扩展点) ---------------------------------------
from CharAgent.hooks import (
    HookConfigError,
    HookError,
    HookFailure,
    HookFn,
    HookPoint,
    HookRegistry,
    ModelCallPhase,
)

# --- model: ChatModel 协议 + 双适配器 --------------------------------------
from CharAgent.model import (
    ChatModel,
    FinishReason,
    HttpXChatModel,
    ModelConfigError,
    ModelConnectionError,
    ModelError,
    ModelMessage,
    ModelProtocolError,
    ModelResponse,
    ModelStatusError,
    ModelTimeoutError,
    ModelToolCall,
    OpenAIChatModel,
    ToolSpec,
    Usage,
    chat_model_from_env,
    openai_chat_model_from_env,
)

# --- prompt: 提示词集中存放与按名加载 (difficulties #69) -------------------
from CharAgent.prompt import (
    PromptError,
    PromptNotFoundError,
    PromptVariableError,
    load_prompt,
    resolve_model_name,
)

# --- retry: 重试退避 + 幂等键 ----------------------------------------------
from CharAgent.retry import (
    ClaimResult,
    ClaimStatus,
    IdempotencyKey,
    IdempotencyKeyError,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    RetryAttempt,
    RetryCallback,
    RetryConfigError,
    RetryError,
    RetryingChatModel,
    RetryPolicy,
    is_retryable,
    retry_async,
)

# --- stream: 流式事件总线 --------------------------------------------------
from CharAgent.stream import (
    TERMINAL_TYPES,
    TOOL_RESULT_SUMMARY_LIMIT,
    EventBus,
    EventSequenceError,
    EventSink,
    EventType,
    StreamError,
    StreamEvent,
)

# --- tool: @tool 装饰器 + schema 生成 --------------------------------------
# 注意: 装饰器 `tool` 刻意不在本文件导入 (会遮蔽子包 CharAgent.tool, 见模块 docstring)
from CharAgent.tool import (
    Tool,
    ToolActionableError,
    ToolConfigError,
    ToolError,
    ToolExecution,
    ToolSchemaInfo,
    build_manual_schema,
    build_pydantic_schema,
    execute_tool,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ALL_TABLES",
    "BACKEND_NAMES",
    "DEFAULT_CODEC",
    "RUN_STATUS_FOR_OUTCOME",
    "SCHEMA_VERSION",
    "TABLE_NAMES",
    "TERMINAL_RUN_STATUSES",
    "TERMINAL_TYPES",
    "TOOL_RESULT_SUMMARY_LIMIT",
    "AgentLoop",
    "ChatModel",
    "Checkpoint",
    "CheckpointCapabilities",
    "CheckpointCapabilityError",
    "CheckpointCodec",
    "CheckpointConfigError",
    "CheckpointError",
    "CheckpointMetadata",
    "CheckpointMigrationError",
    "CheckpointRow",
    "CheckpointSaver",
    "CheckpointSerializationError",
    "CheckpointSource",
    "CheckpointState",
    "CheckpointStorageError",
    "ClaimResult",
    "ClaimStatus",
    "DataConfigError",
    "DataStoreError",
    "Database",
    "DbError",
    "EventBus",
    "EventSequenceError",
    "EventSink",
    "EventType",
    "FinishReason",
    "GuardConfigError",
    "HookConfigError",
    "HookError",
    "HookFailure",
    "HookFn",
    "HookPoint",
    "HookRegistry",
    "HttpXChatModel",
    "IdempotencyKey",
    "IdempotencyKeyError",
    "IdempotencyStore",
    "InMemoryCheckpointSaver",
    "InMemoryIdempotencyStore",
    "InvalidTransitionError",
    "LoopConfigError",
    "LoopGuard",
    "LoopOutcome",
    "LoopResult",
    "Message",
    "MessageRole",
    "MessagesRepository",
    "ModelCallPhase",
    "ModelConfigError",
    "ModelConnectionError",
    "ModelError",
    "ModelMessage",
    "ModelProtocolError",
    "ModelResponse",
    "ModelStatusError",
    "ModelTimeoutError",
    "ModelToolCall",
    "OpenAIChatModel",
    "PgDatabase",
    "PgRepository",
    "PostgresCheckpointSaver",
    "PromptError",
    "PromptNotFoundError",
    "PromptVariableError",
    "RedisCheckpointSaver",
    "RetryAttempt",
    "RetryCallback",
    "RetryConfigError",
    "RetryError",
    "RetryPolicy",
    "RetryingChatModel",
    "Run",
    "RunContext",
    "RunStatus",
    "RunsRepository",
    "StreamError",
    "StreamEvent",
    "Suspension",
    "Thread",
    "ThreadStatus",
    "ThreadsRepository",
    "Tool",
    "ToolActionableError",
    "ToolCall",
    "ToolCallStatus",
    "ToolCallsRepository",
    "ToolConfigError",
    "ToolError",
    "ToolExecution",
    "ToolProvider",
    "ToolSchemaInfo",
    "ToolSpec",
    "TranscriptLine",
    "TruncationStrategy",
    "TurnPair",
    "TurnRecord",
    "Usage",
    "assistant_answer",
    "build_manual_schema",
    "build_pydantic_schema",
    "build_saver",
    "build_tool_call",
    "can_transition",
    "chat_model_from_env",
    "check_identifier",
    "checkpoint_saver_from_env",
    "checkpoints",
    "conversation_turns",
    "count_visible",
    "echo_enabled",
    "ensure_transition",
    "execute_tool",
    "format_history",
    "is_retryable",
    "load_prompt",
    "messages",
    "metadata",
    "migrate_body",
    "model_to_dict",
    "openai_chat_model_from_env",
    "pending_tool_calls",
    "postgres_dsn",
    "resolve_model_name",
    "retry_async",
    "run_status_for_outcome",
    "runs",
    "sqlalchemy_url",
    "summarize",
    "threads",
    "tool_calls",
    "visible_transcript",
]
