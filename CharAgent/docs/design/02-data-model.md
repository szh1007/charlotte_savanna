# 02 数据模型

> 核心实体（thread / run / message / tool_call / checkpoint）**P0 一次定死**（含 schema 版本号，向前兼容 #5）；event 表（事件溯源）P2 追加，见 §5。
> 存储分布：checkpoint 历史 → Postgres（`charagent_` 前缀，框架自有迁移链）；checkpoint 快照 → Redis（默认一条 Stream 记全历史，也可配成只留最新一帧）；业务数据（订单 / 商品 / 退款单 / 余额流水）→ MySQL，**经内部网关访问 business system 的 internal API**（ADR-0008，agent 不直连库）；业务侧自有表（tickets / escalations / approvals / audit_logs）→ Postgres，属 `CharService/` 的**独立迁移链**（2026-09-18）；向量 → Milvus。

## 1. 核心实体定义（P0 定死）

### Thread（会话）

| 字段 | 类型 | 说明 |
|------|------|------|
| thread_id | str (UUID) | 全局唯一 |
| tenant_id | str | 多租户（#32），P1 起所有查询强制过滤 |
| user_id | str | 会话属主 |
| title | str | 会话标题（首条用户消息生成） |
| status | enum | active / closed / escalated |
| created_at / updated_at | datetime | |

### Run（一次执行）

| 字段 | 类型 | 说明 |
|------|------|------|
| run_id | str (UUID) | 全局唯一 |
| thread_id | str (FK) | 所属会话 |
| status | enum | created / running / waiting_tool / waiting_user / retrying / failed / finished / cancelled（#16 状态机） |
| request_id | str | 幂等键载体（#17），同 request_id 重复提交直接返回已有 run |
| model | str | 本次 run 使用的模型（版本化 #40 的基础） |
| prompt_version | str | 使用的 prompt 版本（#40） |
| total_tokens / total_cost | int / decimal | 本 run 累计（#34 成本归因按 task） |
| turn_count | int | 已执行 Turn 数 |
| error | jsonb | 失败原因（结构化，供审计与降级判断） |
| created_at / updated_at / finished_at | datetime | |

### Message（消息）

| 字段 | 类型 | 说明 |
|------|------|------|
| message_id | str (UUID) | |
| thread_id | str (FK) | |
| run_id | str (FK, nullable) | 归属执行（手工人工回复无 run） |
| role | enum | user / assistant / tool / system |
| content | text | 文本内容（assistant 的 final 回答） |
| reasoning | text | assistant 的 reasoning_content，**存储**（供前端折叠展示与审计；与 `hidden` 字段无关，这是内容不是可见性开关）。注意与 wire 历史区分：官方文档要求带 `tools` 的请求回传该字段（称缺失即 400；本机实测 2026-09-11 未触发 400，框架仍按文档执行以保留交错思考） |
| tool_call_ids | jsonb | 该 assistant 消息关联的 tool_call 列表（保持并行语义 #1） |
| hidden | bool | 内部消息（系统注入、压缩摘要）不对前端展示 |
| created_at | datetime | |

### ToolCall（工具调用）

**主键是三列复合**：`(run_id, message_id, tool_call_id)` —— 上游每轮都从 `call_0` 重新编号，
同一个 run 里会出现好几条同名调用。单列主键第二次就撞；只加 `run_id` 也只解决「跨 run」，
同一个 run 的多轮之间仍会撞。真正唯一的身份是「**哪条 assistant 消息**发起的这一次调用」。

| 字段 | 类型 | 说明 |
|------|------|------|
| run_id | str (FK) | 主键之一：归属哪次执行 |
| message_id | str (FK) | 主键之一：**哪条 assistant 消息发起的**（不可空；消息没了它也跟着删） |
| tool_call_id | str | 主键之一：模型返回的 tool_call.id |
| tool_name | str | 调用的工具 |
| arguments | text | 模型填的参数，**原样 JSON 字符串**（不预解析 —— 畸形 JSON 正是自纠错路径的信号 #2） |
| status | enum | pending / running / succeeded / failed / cancelled / needs_approval（HITL #25） |
| result | jsonb | 执行结果（成功）或可操作错误信息（#2：明确字段格式问题而非甩 422） |
| duration_ms | int | 耗时 |
| approved_by / approved_at | str / datetime | HITL 审批信息（#25） |
| created_at / updated_at | datetime | 发起时刻 / 状态最后变化时刻 |

### Checkpoint（快照）

| 字段 | 类型 | 说明 |
|------|------|------|
| checkpoint_id | str (UUID) | |
| thread_id | str (FK) | 分区键 |
| run_id | str (FK) | |
| turn_number | int | 第几个 Turn 的快照（「执行到哪一步」#5） |
| schema_version | int | **序列化 schema 版本号（#5 向前兼容）** |
| state | jsonb | **进度**：消息列表 + 计数器 + 挂起点（恢复才用；序列化协议见 §3） |
| metadata | jsonb | **观察值**：来源（loop/fork/suspension）+ 本轮 token 与耗时 + 工具 + 结束原因（给人看，回放调试用；v3 起） |
| parent_id | str (FK, nullable) | 分支来源（time-travel 回溯 #5：恢复历史时刻 → 新分支） |
| created_at | datetime | |

### 索引一览（6 条，与 `db/schema.py` 逐条对应）

索引只有这些 —— 每一条都对应一种真实查法，不是「先建着以后可能用得上」：

| 索引 | 表 | 列 | 服务哪种查法 |
|------|----|----|-------------|
| `ix_charagent_threads_tenant_updated` | threads | tenant_id + updated_at | 管理端列某租户的会话（最近活动在前） |
| `ix_charagent_threads_user_updated` | threads | user_id + updated_at | 用户端列自己的会话 |
| `ix_charagent_runs_thread_created` | runs | thread_id + created_at | 一个会话的历次执行 |
| `ix_charagent_messages_thread_created` | messages | thread_id + created_at | 翻会话历史（可见与全量两种读法共用） |
| `ix_charagent_tool_calls_run_created` | tool_calls | run_id + created_at | 「这次运行调了哪些工具」（轨迹断言 #62 / 审计） |
| `ix_charagent_checkpoints_thread_created` | checkpoints | thread_id + created_at + checkpoint_id | 翻快照历史 / 取最新一帧（第三条列保证同毫秒的两帧也有确定顺序） |

> 主键索引由数据库自动建，不在此列。加索引要有对应的查法 —— `db/schema.py` 里每条
> 索引旁都写了它服务什么；`tests/test_db_schema.py::test_expected_indexes_exist`
> 会盯着这张表别漏。

## 2. Postgres DDL 概览（alembic 管理）

```sql
-- 核心五表（全部带 charagent_ 前缀，理由见下）：
--   charagent_threads      (thread_id PK, tenant_id, user_id, title, status, 时间戳)
--   charagent_runs         (run_id PK, thread_id FK, status 状态机, request_id 幂等键, …)
--   charagent_messages     (message_id PK, thread_id FK, run_id FK nullable, role, content,
--                           reasoning, tool_call_ids, hidden)
--   charagent_tool_calls   (run_id + message_id + tool_call_id 三列复合 PK, …)
--   charagent_checkpoints  (checkpoint_id PK, parent_id 自引用 FK ON DELETE SET NULL)
-- 字段见 §1 各实体表；全部带 tenant_id 或经 thread 关联，查询强制过滤 (#32)
--
-- 表定义的**唯一来源**在 P0-7 落地为 CharAgent/db/schema.py（SQLAlchemy 的 Table）：
-- 迁移（alembic/）与快照存储（checkpoint/postgres.py）都从那一份取，改字段只有一处要动。
-- 在此之前是 checkpoint/utils/ddl.py 的手写 SQL —— issue 08 把它并入了 schema.py。
--
-- 表名为什么一律带前缀：本项目各子项目共用同一个 PG 库，而
--   langgraph-checkpoint-postgres 也建一张叫 checkpoints 的表 —— 同名会让后者的
--   setup() 静默跳过建表、INSERT 时报「列不存在」，报错与真因（撞名）毫无关系
--   （2026-09-13 实测）。threads / runs / messages 同样是极常见的表名，故一并加前缀。
--
-- 身份列一律 VARCHAR(128) 而非 TEXT（早前那张 checkpoints 表用的是 TEXT，行为一致但不统一）

-- 业务表（2026-09-18 移出，属 CharService/，不再是框架契约）
--   tickets / escalations / approvals / audit_logs 四张表落 CharService 自有的
--   Postgres 迁移链（版本表 charservice_alembic_version），表名带 charservice_ 前缀。
--   框架侧不再建它们。原 approvals 表还曾把业务对象写进字段枚举
--   （operation(退款/赔付/通知)）—— 那正是策略漂进框架的典型，一并移走。
--   挂起状态本身存在 checkpoint 的 state.suspension 里，不需要框架建审批表。

-- 基础设施（P1）
idempotency_keys:   request_id PK, run_id FK, status, created_at, expires_at   -- #13/#17
```

## 3. Checkpoint 序列化协议

> 实现落点 `CharAgent/checkpoint/`（issue 07）：协议 `serialization.py`，三实现
> `memory.py` / `redis.py` / `postgres.py`，配置切换 `config.py`，静态零件 `utils/`（含 `history.py`：把一串快照渲染成可读表格的历史视图）。
> **表定义的唯一定义处是 `db/schema.py`**（P0-7 起）：快照存储首次写入时用它幂等建表
> （`PgDatabase.create_tables`），alembic 迁移也在 P0-7 起从同一份定义出发 —— 此前那两处
> 各自持有一份手写 SQL（`checkpoint/utils/ddl.py`），issue 08 统一到了 SQLAlchemy 的 Table。

| 项 | 决策 | 实现现状（P0-6） |
|----|------|-----------------|
| 主格式 | **JSON**（安全、跨语言；#5 明确 pickle 有安全风险） | `CheckpointCodec`：`ensure_ascii=False`、`allow_nan=False`（NaN 不是合法 JSON，宁可报错）；存存储用紧凑一行，给人读用 `indent=2` |
| 自定义序列化器 | message 中的工具结果含 datetime / 嵌套 dict，注册自定义 encoder / decoder（#5「对象不可序列化」） | 标签机制 `{"__charagent_type__": "datetime", "value": "..."}`（恰好两个键才算标签，业务数据同名不受影响）；出厂只带 datetime，其余 `codec.register_type(...)` 注册；装不进又没注册的类型报错并**给出注册示例** |
| 进度 / 观察值分层 | 快照分两块（v3 起）：**进度**恢复才用（消息历史 + 计数器 + 挂起点），**观察值**给人看（来源 / 本轮用量 / 工具 / 结束原因） | `CheckpointState` 与 `CheckpointMetadata` 两个数据类；存储上也是两处（Redis 记录里的两个字段、Postgres 的两个 JSON 列）；迁移函数收的是「主体」（`state` + `metadata`）—— 真实迁移常要在两者之间搬字段（v2→v3 就是搬的） |
| schema_version | 每份快照带版本号；升级时写迁移函数（旧版本 → 新版本），保证旧会话可读回（#5 向前兼容） | 当前 `SCHEMA_VERSION = 3`（v1 = state 为裸消息列表；v2 = 结构化字典；v3 = 观察值从 state 搬进 metadata）；`utils/migrations.py` 的 `MIGRATIONS` 逐级升级、缺函数即报错；版本**比当前新则拒读**（硬猜会把字段读错位） |
| Redis 存储 | Stream 流式全历史（默认）：`charagent:ckpt:{thread_id}` 一条流水账，`XADD` 追加 + `XRANGE` 翻历史 + `MAXLEN` 裁剪 + `TTL` 过期；可选 `mode="latest"` 只留最新一帧（弱一致，快） | 配置 `CHARAGENT_CHECKPOINT_REDIS_MODE` / `CHARAGENT_CHECKPOINT_REDIS_MAX_FRAMES` / `CHARAGENT_CHECKPOINT_KEY_PREFIX`；latest 模式下 `load(id)` / `list_history` 明确报 `CheckpointCapabilityError`；按编号取帧要一并给 thread_id（帧按会话分区；latest 的键名带 `latest` 与流键区分开） |
| Postgres 存储 | `charagent_checkpoints` 表逐条（强一致、历史、time-travel：恢复旧 checkpoint 产生新分支 parent_id） | 身份字段成列 + `state` / `metadata` 两个 JSONB 列（表结构与「为什么带前缀」见 §2）；一帧一行、`ORDER BY created_at, checkpoint_id`；**同步驱动 + `asyncio.to_thread`** —— psycopg 的异步连接在 Windows 默认事件循环（Proactor）上不可用；**实现走 SQLAlchemy**（P0-7 统一），与仓储共用 `db/database.py` 的连接层 |
| 快照时机 | 每 Turn 结束后（含挂起点 #25：挂起也落快照，审批后从该点恢复而非重跑） | `AgentLoop(saver=..., thread_id=...)` 在每轮 `_record_turn` 末尾落一帧（**先落盘再触发 after_turn hook**；落盘失败向上抛），同时写下来源（正常一轮 `loop` / 从快照续跑的第一帧 `fork` / 补做挂起调用的那帧 `suspension`）；挂起点机制已备（`Suspension` + `pending_tool_calls`，`resume` 会补做欠下的调用再继续），**触发**归 P1-7 |

存储介质带来的能力差异（`saver.capabilities` 可查，ADR-0002 的对比面）：

| 实现 | 历史 | 会过期 | 备注 |
|------|------|--------|------|
| InMemory | 有 | 不会 | 测试 / 单机；进出都深拷贝；排序与 Postgres 对齐（按时刻，非插入顺序） |
| Redis（history，默认） | 有 | 可配 TTL | 快、可裁剪（`MAXLEN`）；按会话分区存放（按编号取帧要带会话号） |
| Redis（latest） | 无 | 可配 TTL | 只要断点续跑的会话缓存（对应 langgraph 的 ShallowRedisSaver） |
| Postgres | 有 | 不会 | 强一致；能按任意字段查（身份字段都是列） |

## 4. Milvus Collection 设计（P1）

| 项 | 决策 |
|----|------|
| Collection | `support_knowledge`（database: charlotte，本机 Milvus） |
| 向量 | `text-embedding-3-large`，dim = 1536 |
| Index | HNSW（M=16, efConstruction=200），metric = COSINE |
| 标量字段 | `doc_id`（父子索引：父块喂模型）、`chunk_id`、`tenant_id`、`user_id`、`source`（文档名/URL）、`category`（退换货/物流/发票...）、`created_at` |
| 过滤 | 检索强制 `filter="tenant_id == 'xxx'"`（#32），category 可组合过滤 |
| 摄取 | pypdf 解析 PDF → 清洗（去重/格式归一化）→ 元数据提取 → chunk（size≈500, overlap≈50）→ 向量化写入；增量更新走 upsert |
| 检索 | 粗召回（top_k=20）→ rerank（P2，SPI 预留）→ 取 top-5 喂模型，答案带引用溯源（#29 citation，返回 doc_id + chunk 文本） |

## 5. P2 追加结构（不回溯改造 P0/P1）

| 结构 | 说明 |
|------|------|
| `events` 表 | 事件溯源（#12）：每次状态变化以不可变事件追加（event_type / run_id / payload / seq），支撑 trace 回放（#59）与审计 |
| `memories` 表 | 四层记忆（#31），软删除标记（#33） |
| `cost_entries` | 成本明细（#34 按 task 归因） |
| TTL 策略 | 会话过期清理、checkpoint 历史保留策略（#12） |
