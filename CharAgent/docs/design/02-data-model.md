# 02 数据模型

> 核心实体（thread / run / message / tool_call / checkpoint）**P0 一次定死**（含 schema 版本号，向前兼容 #5）；event 表（事件溯源）P2 追加，见 §5。
> 存储分布：checkpoint 历史 + demo 业务表 → Postgres（alembic 管理）；checkpoint 快照 → Redis KV；订单 → MySQL 只读；向量 → Milvus。

## 1. 核心实体定义（P0 定死）

### Thread（会话）

| 字段 | 类型 | 说明 |
|------|------|------|
| thread_id | str (UUID) | 全局唯一 |
| tenant_id | str | 多租户（#32），P1 起所有查询强制过滤 |
| user_id | str | 会话属主 |
| title | str | 会话标题（首条用户消息生成） |
| status | enum | active / closed / escalated |
| created_at / updated_at | datetime | 索引：tenant_id + updated_at |

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
| created_at / updated_at / finished_at | datetime | 索引：thread_id + created_at |

### Message（消息）

| 字段 | 类型 | 说明 |
|------|------|------|
| message_id | str (UUID) | |
| thread_id | str (FK) | |
| run_id | str (FK, nullable) | 归属执行（手工人工回复无 run） |
| role | enum | user / assistant / tool / system |
| content | text | 文本内容（assistant 的 final 回答） |
| reasoning | text | assistant 的 reasoning_content，**存储但不回填历史**（#11） |
| tool_call_ids | jsonb | 该 assistant 消息关联的 tool_call 列表（保持并行语义 #1） |
| hidden | bool | 内部消息（系统注入、压缩摘要）不对前端展示 |
| created_at | datetime | 索引：thread_id + created_at |

### ToolCall（工具调用）

| 字段 | 类型 | 说明 |
|------|------|------|
| tool_call_id | str | 模型返回的 tool_call.id |
| run_id | str (FK) | |
| tool_name | str | 调用的工具 |
| arguments | jsonb | 模型填的参数（原始 JSON） |
| status | enum | pending / running / succeeded / failed / cancelled / needs_approval（HITL #25） |
| result | jsonb | 执行结果（成功）或可操作错误信息（#2：明确字段格式问题而非甩 422） |
| duration_ms | int | 耗时 |
| approved_by / approved_at | str / datetime | HITL 审批信息（#25） |

### Checkpoint（快照）

| 字段 | 类型 | 说明 |
|------|------|------|
| checkpoint_id | str (UUID) | |
| thread_id | str (FK) | 分区键 |
| run_id | str (FK) | |
| turn_number | int | 第几个 Turn 的快照（「执行到哪一步」#5） |
| schema_version | int | **序列化 schema 版本号（#5 向前兼容）** |
| state | jsonb | 消息列表 + 上下文状态（序列化协议见 §3） |
| parent_id | str (FK, nullable) | 分支来源（time-travel 回溯 #5：恢复历史时刻 → 新分支） |
| created_at | datetime | 索引：thread_id + run_id + turn_number |

## 2. Postgres DDL 概览（alembic 管理）

```sql
-- 核心表：threads / runs / messages / tool_calls / checkpoints
-- （字段见上表；全部带 tenant_id 或经 thread 关联，查询强制过滤 #32）

-- demo 业务表（P1）
tickets:            ticket_id, thread_id FK, user_id, category, status(open/auto_resolved/escalated/closed),
                    priority, created_at, resolved_at
escalations:        escalation_id, ticket_id FK, thread_id FK, reason, assigned_agent, status,
                    created_at, resolved_at
approvals:          approval_id, run_id FK, tool_call_id FK, operation(退款/赔付/通知), amount,
                    context(jsonb, 审批上下文 #25), status(pending/approved/rejected/timeout),
                    requested_at, decided_by, decided_at
audit_logs:         log_id, tenant_id, thread_id, run_id, actor, action, target, detail(jsonb), created_at
                    -- 每次工具调用/数据访问留痕（#27），脱敏后写入

-- 基础设施（P1）
idempotency_keys:   request_id PK, run_id FK, status, created_at, expires_at   -- #13/#17
```

## 3. Checkpoint 序列化协议

| 项 | 决策 |
|----|------|
| 主格式 | **JSON**（安全、跨语言；#5 明确 pickle 有安全风险） |
| 自定义序列化器 | message 中的工具结果含 datetime / 嵌套 dict，注册自定义 encoder / decoder（#5「对象不可序列化」） |
| schema_version | 每份快照带版本号；升级时写迁移函数（旧版本 → 新版本），保证旧会话可读回（#5 向前兼容） |
| Redis 存储 | `ha:ckpt:{thread_id}` → JSON 整存 + TTL（弱一致，快） |
| Postgres 存储 | checkpoints 表逐条（强一致、历史、time-travel：恢复旧 checkpoint 产生新分支 parent_id） |
| 快照时机 | 每 Turn 结束后（含挂起点 #25：挂起也落快照，审批后从该点恢复而非重跑） |

## 4. Milvus Collection 设计（P1）

| 项 | 决策 |
|----|------|
| Collection | `support_knowledge`（database: charlotte，本机 Milvus） |
| 向量 | `text-embedding-3-large`，dim = 1536 |
| Index | HNSW（M=16, efConstruction=200），metric = COSINE |
| 标量字段 | `doc_id`（父子索引：父块喂模型）、`chunk_id`、`tenant_id`、`user_id`、`source`（文档名/URL）、`category`（退换货/物流/发票…）、`created_at` |
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
