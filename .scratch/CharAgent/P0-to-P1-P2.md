# issue10 — P0 → P1/P2 交接清单

> **这是什么**：P0 交付后，**还没被 P1/P2 消费掉的接线信息**。逐条写明「为什么不在 P0、归哪个 issue、落地时要接哪根线」—— 其中 **「P0 已备好的接口」那一列是关键**：它说明后续落地时不用回头改 P0。
>
> **读者**：要开工某个 P1/P2 issue 的人（含未来的自己）。P1/P2 走完后本文档就该自然退休。
>
> **编制**：2026-09-15（issue 10 验收期间对 issue 01~09 的全量复核） | **触发**：issue 10 ticket 第 3 条「之前遗漏的所有属于 P0 的问题都需要闭环」+ 第 4 条「issue10 阶段没法完成的，记录在此」。
>
> **修订 2026-09-18（随 ADR-0008/0009 业务对接改造）**：
> - 原 §4「P0 就这样了的八条边界」**移入设计文档** [`CharAgent/docs/design/06-boundaries.md`](../../CharAgent/docs/design/06-boundaries.md) —— 那类内容寿命跨度是「只要代码还这样」，属于设计文档而非 issue tracker
> - 原 §5「预埋路径待验证清单」两条**下沉为行动项** —— 分别挂进 [`11-P1-1`](issues/11-P1-1-fastapi-server.md) 与 [`17-P1-7`](issues/17-P1-7-hitl-redaction-audit.md) 的 checklist（行动项要待在会被执行的地方）
> - 原 §1「P0 遗漏备案」**删减** —— 12 条明细在 issue 10 §5「顺手补的 P0 遗漏」已有完整记录，此处不再复制
> - 原 §6「环境性事项」**删减**为仍然有效者
> - §1~§1.14（原 §2）按 ADR-0008/0009 **修正 6 处**（原 §2.1 server 拆分、§2.4 Saga 场景、§2.5 限流位置、§2.6 沙箱形态、§2.7 HITL 模型、§2.13/§2.14 已移交）
> - 章节已重排：本文现为 §0 结论 / §1 P0→P1 / §2 P0→P2 / §3 环境性事项
>
> **复核方式**：逐条读 issue 01~09 的 Comments / 遗留 / 未采纳章节，去代码里核实（不是读文档下结论）；全仓 `TODO` / `FIXME` / `HACK` / `NotImplementedError` 命中数为 **0**，无真占位。

---

## 0. 一句话结论

P0 的验收三件套（带工具的 agent loop 跑通 / checkpoint 断点续跑 / mock 单测全绿）**全部达成**（真实端点实测，见 issue 10 的 Comments）；P0 范围内被四轮往后推的「根门面导出」也一并闭环。**剩下的全部是「P1/P2 才有生产代码」的条目** —— 不是漏做，是归属没到。

---

## 1. P0 → P1 交接（生产代码在 P1，接口已在 P0 备好）

按 P1 issue 编号排序。

### 1.1 P1-1 FastAPI 运行时层（REST + SSE + TaskQueue）

> **2026-09-18 修订**：本 issue 已拆为两半 —— 通用运行时端点（会话 / run / SSE / 取消 / healthz）留框架，以 **router 工厂**交付；业务端点（审批台 / 接管台 / 工单）去 `CharService/`。下表按拆分后的框架侧口径。

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| SSE 端点与 `after_event_id` 断点续拉 | 需要 server 才有「流」可推；P0 的事件流只在进程内 | `StreamEvent.seq` 就是 `after_event_id` 的取值；`EventSink` 换成「推 asyncio.Queue」即可，loop 零改动 |
| **token 级流式**（content / reasoning 逐 token delta） | P0 的 `ChatModel.generate` 返回**完整** ModelResponse（非流式），故每次响应只产出一条完整 reasoning delta | 需给 `ChatModel.generate` 加 delta 回调 —— **这是 P1 唯一要动 P0 协议的地方**，改动面：`model/protocol.py` + 两个适配器 + `MockLLM` + `AgentLoop._decide` |
| `run_id` 注入事件 | 框架层没有 run 概念（`StreamEvent` 刻意不带 run_id） | P1 server 转发时注入，事件契约不用改 |
| 事件持久化（重启后仍能续拉） | 需要 `events` 表（P2-2）+ 存储策略 | 无（P2-2 落地后回填） |
| `/healthz` 依赖探活 | server 能力 | 各配置模块已能给连接串（`postgres_dsn` / `sqlalchemy_url` / checkpoint 的 Redis URL 回退） |
| REST 幂等（同 `request_id` 返回已有结果） | 需要 run 表 | `db.repositories.runs.get_by_request_id` 已备（issue 08 F5 补的用例点名「P1-1 接线靠它」）；`IdempotencyStore` 协议 + InMemory 实现已交付 |
| `TaskQueue`（进程内 asyncio FIFO） | server 构件（ADR-0006） | 无（P1 新建） |
| **会话绑定身份**（`user_id` / `tenant_id` + 应用注入委托凭据） | P0 无应用层，身份链路由使用方建立（ADR-0009） | `Thread.tenant_id` / `Thread.user_id` 字段已建且是数据隔离单位；`db.repositories.threads.list_threads` 已支持 `user_id` 过滤 |

### 1.2 P1-2 运行状态机 + 取消

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 状态机的**推进者**（谁在何时把 `running` 改成 `waiting_tool` …） | issue 08 §7 明确「本 issue 交付规则与安全落库入口」 | `db/state.py` 的 `ALLOWED_TRANSITIONS` / `can_transition` / `ensure_transition` / `run_status_for_outcome`；`RunsRepository.try_transition`（带条件的 UPDATE 做乐观锁） |
| `error(cancelled)` 事件 | 框架在 `CancelledError` 传播路径上**不能 await**（无法安全发事件），故由 server 产出 | `_KillSwitch` 展示的取消语义（cancel → 收尾 → 再上报）在 CLI 已验证；`AgentLoop` 不吞 `CancelledError` |
| `POST runs/{id}/cancel` 接口 | server 能力 | 同上 —— kill switch 的机制层（`task.cancel`）在 P0 已闭环 |
| 取消后的资源释放与幂等键留痕 | 需要 run 表与幂等表（P1 迁移） | `IdempotencyStore.release` 只放行在途记录（结果不被后到的失败抹掉） |
| **挂起类型区分**（内部审批 / 用户确认） | 2026-09-18 新增：两种挂起语义需在状态机上分开（ADR-0009） | `RunStatus` 枚举与 `ALLOWED_TRANSITIONS` 是唯一改动点；`Suspension` 已能携带 `reason` |

### 1.3 P1-3 熔断 + 分层超时

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 熔断 + failover | issue 06 §8 明确「连续 429 靠三条上限兜底，熔断属 P1-3」 | `RetryPolicy` 的三个上限（次数 / 总耗时 / 单次封顶）已封死放大；`RetryAttempt` 记录单可供熔断器判断 |
| 分层超时（model 60s / tool 10-30s / run 总超时） | 需要 server 编排层 | model 侧已有单请求 timeout（适配器构造参数 `timeout=60.0`）；run 侧有 `LoopGuard` 的 wall-clock 软限制；tool 侧**没有超时**（见 [`06-boundaries.md` §6.1](../../CharAgent/docs/design/06-boundaries.md)） |
| **通用下游依赖熔断 + `DEPENDENCY_DOWN`** | 2026-09-18 新增：下游依赖故障是 P0 不存在的场景（ADR-0008）。错误码用通用名（原 `GATEWAY_DOWN` 已改名 —— 框架不该知道业务拓扑）；**降级出口归应用侧**（边界复核） | 熔断三态与模型侧共用同一实现；`03-api.md §4` 错误码表追加一项 |

### 1.4 P1-4 幂等存储 + Saga + 分布式锁

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| Redis / PG 的幂等存储 + TTL | issue 06 §8「P1-4」 | `IdempotencyStore` 协议（claim / complete / release）+ `ClaimStatus` 三态已定死 |
| Saga 补偿 | 需要真实副作用 | `ClaimResult` 的 `claimed` 语义（只有 CLAIMED 才执行真实动作）。**2026-09-18 更新**：施展场景从泛化的「退款/通知」变为**异步退款链路**（`pending → processing → completed \| failed`，同事务改余额 + 写流水 + 置状态），落点见 `.scratch/CharService/issues/06`。幂等键由此从「可选」变成**刚需** |
| 分布式锁（Redis SETNX + TTL 续租 + owner 校验） | P1 新建 | 无 |
| 幂等表迁移 | `db/README.md` 已写追加流程 | 五表已进 alembic；`0002` 起按 README 的流程追加 |

### 1.5 P1-5 限流

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 固定/滑动窗口、令牌桶/漏桶 | P1 新建 | `RATE_LIMITED` 错误码已在 `03-api.md §4` 占据位置；`db.Thread.tenant_id/user_id` 字段已建（限流键的维度来源） |
| per user / tenant / model **+ per tool** | 2026-09-18 新增 per tool | 同上 |
| **部署位置：网关而非 agent 进程内** | **2026-09-18 修订**（ADR-0008）：限流若在 agent 侧，写个新 client 就绕过了；网关是访问业务系统的唯一入口，策略在那里才真正生效 | 框架侧交付物不变（四种算法的 `RateLimiter` 实现），只是调用方变成网关。落点见 `.scratch/CharService/issues/04` |

### 1.6 P1-6 安全护栏（输入/输出 + 沙箱 + SSRF）

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 四层纵深护栏、prompt injection 防护 | P1 新建 `guard/` | `content_filter` 的**终止语义**在 P0 已定：loop 层只终止循环，拦截语义归调用方（`FinishReason.CONTENT_FILTER` + `terminate_error_code` 已判它为终局 error） |
| 工具沙箱 / 最小权限 | 需要真实业务工具 | **2026-09-18 修订**（ADR-0008）：沙箱从「只读账号 + SQL 白名单」改为**出站目标白名单**（工具只能访问配置的网关地址）+ 参数校验；业务数据侧的最小权限由业务系统的 internal 端点承担。#24 难点仍覆盖，落点不同。`@tool` 的「一个工具一件事」写进 `tools_demo.py` 的写法约定（业务工具照抄） |
| **越权身份不可构造** | 2026-09-18 新增（ADR-0009） | `user_id` 不在工具签名中 → 注入诱导模型填他人 id 无处可填。工具签名的运行时注入点见 `.scratch/CharService/issues/01` |
| SSRF 防护 | P1 构件 | 无 |

### 1.7 P1-7 HITL + 脱敏 + 审计

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| **挂起点的触发**（哪些工具需审批） | issue 07 §8 明确「P0 交付的是能存能读能恢复的机制」 | `checkpoint.utils.types.Suspension`（reason + pending + approval_id）已可存取；`pending_tool_calls(history)` 能从 wire 形状里找出欠结果的调用；`AgentLoop.resume` 会**先补做**它们再继续（不被 guard 影响） |
| `approval_required` 事件 | 由 loop 之外的审批模块产出，不参与 P0 状态机 | `EventType` 已是可扩展枚举；`EventPrinter` 对未知事件类型有兜底分支（原样打印，不 KeyError）—— 已在 `tests/test_client_render.py` 钉住 |
| 审批表 + 超时降级（默认 15 分钟） | P1 迁移（`db/README.md` 的追加流程） | `ToolCallStatus.NEEDS_APPROVAL` 等 6 个取值已在 `db/entities.py` 定义 |
| **金额分层 + 角色分离** | **2026-09-18 新增**（ADR-0009）：≤ 阈值自动通过；发起方与审批方不得为同一人 | `ToolCall` 的审批信息字段位已留；`ClaimResult` 的 claimed 语义可复用为「自动通过」判定 |
| **用户确认（第二条挂起路径）** | **2026-09-18 新增**（ADR-0009）：结构化 `confirm_token` + `confirmation_required` 事件，与内部审批**代码不耦合** | `EventType` 可扩展（同 `approval_required`）；`Suspension` 可承载凭据字段 |
| PII 结构化脱敏、审计轨迹 | P1 新建模块 | `audit_logs` 表待 P1 迁移 |
| **审计双粒度**（工具调用 + 数据访问） | **2026-09-18 修订**（ADR-0009）：数据访问粒度含敏感字段命中标记 | 工具粒度可挂 `HookPoint.ON_TOOL_EXECUTED`；数据访问粒度在网关侧产生（`.scratch/CharService/issues/04`） |

### 1.8 P1-8 降级兜底

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 模板回复 / FAQ 匹配（Redis）/ 部分结果 / 转人工 | server 层能力 | 框架只给**事实性说明**（`TERMINAL_ERROR_TEXT`），降级话术归 server —— 这条分工在 issue 05 §3 已定案；`LoopOutcome` 的六种结束原因就是 server 选降级路径的输入 |
| **`DEPENDENCY_DOWN` 分支** | **2026-09-18 新增**（ADR-0008）：下游依赖不可用。错误码用通用名（原 `GATEWAY_DOWN` 已改名 —— 框架不该知道「网关」这个业务拓扑）；「只读仍可用 / 写操作拒绝并建议转人工」这条**出口策略归应用侧** | 同 `LoopOutcome` 机制；`03-api.md §4` 错误码表追加一项 |

### 1.9 P1-9 RAG（Milvus）

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 摄取 / 检索 / rerank / `final.citations` | P1 新建 `rag/` | 事件载荷是 dict（加 `citations` 键不改协议结构）；CLI 的 `EventPrinter` 对未知键不做假设 |

### 1.10 P1-10 结构化输出 / 上下文压缩

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| `response_format` / JSON schema 约束输出 | `model/__init__.py` 的「已知能力边界」已列（strict 模式 Beta / tool_choice / logprobs 等均未实现） | `_resolve_payload` 的「None 不携带」语义已定，加参数不用改架构 |
| 上下文压缩（摘要 + 截断不破坏 tool 调用结构） | issue 04 只做了 **length 截断**（CONTINUE / CONDENSE） | `TruncationStrategy` + `content_parts` 拼合链 + `max_truncations` 已跑通；摘要式压缩要新增策略成员 |
| `prefix: True` 对话前缀续写 | issue 04 §4 评估后**明确不采用**（四条理由：beta 通道 / 末条必须是 assistant 与 tool 配对冲突 / 与思考模式交互未定义 / 本意是格式引导） | 不涉及 |

### 1.11 P1-11 token 计量与预算硬上限

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 按 task 归因 + 预算硬上限 + **重试双计费的账** | issue 06 §4 明确「P0 不并入 LoopGuard 的 token 预算（避免假账），裁决属 P1-11」 | 被丢弃的用量随 `on_retry` 的 `RetryAttempt.result`（原样 ModelResponse，可读 usage）交回调用方；`retry_upstream_interrupted=False` 开关也已备（预算/缓存要「不再烧一次」时用） |
| `BUDGET_EXCEEDED` 错误码 | `03-api.md §4` 已占位（标 P2） | `LoopOutcome.TOKEN_BUDGET` 已能刹车（软限制） |

### 1.12 P1-12 结构化日志 + 指标

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| structlog JSON（request_id / trace_id / thread_id 贯穿 + 脱敏前置）、Prometheus 文本指标 | P1 新建 `logging/` | `HookPoint.ON_EVENT` / `ON_MODEL_CALL` / `ON_TOOL_EXECUTED` 五个触发点已可挂（空注册零开销，异常隔离留痕 `registry.failures`）；`retry` 的 `on_retry` 序列可挂多个观测回调 |
| token 计量打点 | 见 1.11 | 同上 |

### 1.13 P1-13 客服 demo 业务层 —— **已整体移交 `CharService`**

> **2026-09-18**：原设计（只读直连 minimall MySQL + demo 自建业务表）被 ADR-0008 推翻。本 issue 拆为 `.scratch/CharService/issues/01~10` 十个垂直切片，规格见 `.scratch/CharService/PRD.md`。**下表保留原 P0 侧准备的接口** —— 其中多数仍然有效，只是消费方换成了 CharService 的对应 issue。

| 项 | 为什么不在 P0 | P0 已备好的接口 | 现落点 |
|----|--------------|----------------|--------|
| 业务工具集 | 需要真实数据源与业务表 | `tools_demo.py` 是**写法模板**（Annotated + Field、可操作错误、一个工具一件事）；`query_order_status` 就是它的最小样例 | CharService `tools/`（issues 03/05/07/08） |
| 业务数据访问 | P1 构件 | `db/PgDatabase` 是「同步引擎 + to_thread」的样板 | **改为经网关访问** internal API（ADR-0008），不再有「MySQL 只读层」 |
| 业务表迁移 | `db/README.md` 已写追加流程 | 五表 + alembic 已就位（`0001_charagent_core`，版本表带 `charagent_` 前缀防撞） | CharService 自有表用 `charservice_` 前缀（见 `06-boundaries.md` §6.6） |
| 转人工接管流程 | P1 构件 | `db.conversation.visible_transcript` / `conversation_turns` 已能给出「给前端看的会话」（一问一答，不含 system/tool） | CharService issue 09 |
| 退款幂等 | 见 1.4 | 同 1.4 | CharService issues 06/07 |

### 1.14 P1-14 Vue 前端 —— **已整体移交 `CharService`**

> **2026-09-18**：界面（/chat + /admin）完全是客服业务，不属于业务无关的框架包（ADR-0008 决策 9 分层剥离）。移交后**扩展**：新增结构化确认卡片（ADR-0009）与审批台角色区分。见 `.scratch/CharService/issues/10`。

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| `/chat` + `/admin` 双路由，EventSource 消费 | P1 构件 | 事件契约（六类 + seq + 四条不变量）已在 P0 定稿并测试；CLI 的 `EventPrinter` 是「同一份事件流的另一种渲染」，可当渲染规则的参照 |
| **结构化确认卡片** | 2026-09-18 新增（ADR-0009） | `EventType` 可扩展 `confirmation_required`；`confirm_token` 的携带形态由 CharService 定 |
| Vitest 渲染测试 | P1 后期 | `tests/fixtures/snapshots/event_stream_tool_path.json` 是现成的事件序列样本 |

### 1.15 P1-15 运行时上下文注入 + 挂起信号通道（2026-09-18 新增，ADR-0010）

> 这是**复核时才发现无主**的一环：P0 的工具全是纯函数，所以「工具从哪拿身份」与「工具怎么让 loop 停下」两个方向都不需要通道；P1 的业务场景把两件事同时变成刚需，但四个文档各指向一个方向，机制没人认领。详见 [issue 35](issues/35-P1-15-runtime-context-suspension.md)。

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| **loop → 工具**：运行时上下文注入 | P0 工具无身份需求 | `Tool` 数据类与 `@tool` 装饰器是唯一改动面（加 `Injected()` 标记与 schema 跳过）；`execute_tool` 是唯一调用入口（加 `context=` 形参） |
| **工具 → loop**：挂起信号 | P0 工具无副作用 | `checkpoint/utils/types.py` 的 `Suspension(reason/pending/approval_id)` 已可存取；`CheckpointSource.SUSPENSION` 已定义；`pending_tool_calls(history)` 已能找出欠结果的调用；`AgentLoop.resume` 已会**先补做**再继续 |
| 状态机补态 | 原 8 态只有 `waiting_user` | `RunStatus` 枚举 + `db/state.py` 的 `ALLOWED_TRANSITIONS` 是改动面 |
| 控制面仓储查询 | server 能力 | `RunsRepository` 已有 `add/get/get_by_request_id/try_transition/set_status`，**只缺按状态列** |
| HTTP 端点 | 同 P1-1 | 归 **P1-1**（`GET /runs?status=` + `POST /runs/{id}/resume` + 管理端 `GET /runs/{id}/transcript`） |

**关键事实（复核实测）**：`loop.py` 对 `Suspension` 的引用数为 **0**，`ToolCallStatus.NEEDS_APPROVAL` 的生产写入者为 **0** —— 即**记录位齐备、产生者为零**：框架能**从**挂起恢复，但**不会产生**挂起。这条缺口不补，P1-7 的核心验收（挂起 → 审批 → 恢复）无法开工。

---

## 2. P0 → P2 交接

> **2026-09-18**：P2 的 11 个 issue 均**不受** ADR-0008/0009 业务改造影响（耦合是单向的：P2 消费 P1 的产物）。本表未做修改。

| 项 | 归属 | 为什么不在 P0/P1 | P0 已备好的接口 |
|----|------|----------------|----------------|
| `events` 表（事件溯源 #12） | P2-2 | issue 08 §7 明确「P2 迁移」 | 迁移体系与 `db/README.md` 的追加流程已备；`StreamEvent` 形状即表结构草案 |
| checkpoint 历史的保留与清理策略 | P2-2（#12） | issue 07 §7 明确不做 | Redis 侧已有 `CHARAGENT_CHECKPOINT_TTL_SECONDS` / `CHARAGENT_CHECKPOINT_REDIS_MAX_FRAMES`；Postgres 侧无清理（全历史是它的卖点） |
| 数据合规级联删除（向量 / checkpoint / trace） | P2-11（#28） | issue 07 §7 明确 | `PostgresCheckpointSaver.delete_thread` 已实现（Postgres 专有，不在协议里 —— 协议 docstring 已写明这条边界） |
| 连接池调优 / 无状态水平扩展 | P2-10（#66） | issue 07 初版的「一条连接 + 一把锁」已被 issue 08 的 `PgDatabase` 连接池取代；调优本身属 P2 | `PgDatabase` 支持注入引擎（`test_injected_engine_survives_dispose` 钉住 dispose 不关注入的引擎） |
| 四层记忆 / 成本追踪 / 可观测 / 多 agent / MCP / Skills / Eval / 上下文工程 | P2 的 8 个插件 | 全部走 ADR-0007 的三类扩展点挂载，**不回溯改造 P0/P1** | 事件总线（六类事件即 P2 订阅源）+ hook 注册表（五触发点，空注册零开销）+ SPI（`ChatModel` / `CheckpointSaver` / 待建的 `EmbeddingProvider` / `ModelRouter` / `SemanticCache`） |
| **MCP 插件的真实素材** | P2-7（#50） | 2026-09-18 更新：P1 的工具层按 ADR-0008 保持「薄封装」，P2-7 落地时把同一批工具**零改动**包成 MCP server 暴露——素材从「外部公开 server」升级为「自家真实业务工具」 | 工具注册表（`@tool` 装饰器）即遍历入口 |
| 语义缓存（防重复调用 LLM） | P2-4（#36） | issue 06 §8 | `retry_upstream_interrupted` 开关已备 |
| 非确定性统计（多次运行看通过率 #62） | P2-8 评估体系 | issue 09 §8 定案：默认用例全替身本身确定，没有可统计的对象 | 录制回放样本（3 份真实样本）+ `MockLLM` 三模式已就位 |
| trace 回放 / 导出轨迹文件 | P2（#59） | issue 07 §10「不导出轨迹文件（用户本轮未选）」 | 每帧已有观察值（`CheckpointMetadata`）+ 可读历史视图（`format_history`），CLI 的 `/history` 就是它的消费方 |
| Redis checkpoint 的编号索引键（免翻账本） | 未采纳（issue 07 §10） | 帧数上量后再加（LangGraph 用 JSON + RediSearch） | 现在按编号取帧是「先便宜的 JSON 解析比对编号，命中才完整解码」 |
| super-step 粒度存帧（LangGraph 那种「每节点一帧 + writes 表」） | 未采纳（issue 07 §10） | 本项目的粒度是「每 Turn 一帧 + 挂起点写在 state 里」，已够「恢复而非重跑」；窗口问题见 [`06-boundaries.md` §6.8](../../CharAgent/docs/design/06-boundaries.md) | `CheckpointSource` 已能标记帧的来源（loop / fork / suspension） |

---

## 3. 环境性事项

| 项 | 状态 | 说明 |
|----|------|------|
| `python -m CharAgent.client` 要求 cwd = 仓库根 | 已知 | 无打包元数据（`pyproject.toml` 只有 `[tool.ruff]`），靠 `pytest.ini` 的 `pythonpath` 与 cwd 兜底。装包 / 入口脚本属 P2-10 工程化 |
| 真实端点演示的 token 消耗 | 正常 | CLI 演示一轮工具问答约 3K tokens；`pytest -m integration` 11 例约 25s |
| 本机 Redis 未必已启动 | 环境 | `pytest -m redis` 需先起 Redis；CLI 的 `--backend redis` 同理。Postgres 侧可用（`pytest -m "pg or pg_db"`） |
