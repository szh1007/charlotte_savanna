# issue10 — P0 → P1/P2 交接清单

> **这是什么**：issue 10（P0 验收线）实施期间，对 issue 01~09 做了一次「当时说要做的，现在到底做了没有」的全量复核。本文档收两类东西：
> 1. **P0 范围内但当时漏做、已在本轮闭环的**（§1，备案用 —— 证明「遗留」不是被无声吞掉）；
> 2. **P0 阶段做不到、需要 P1/P2 支持的**（§2~§4，逐条写明为什么不在 P0、归哪个 issue、落地时要接哪根线）。
>
> **编制**：2026-09-15 | **触发**：issue 10 ticket 第 3 条「之前遗漏的所有属于 P0 的问题都需要闭环」+ 第 4 条「issue10 阶段没法完成的，记录在此」。
> **复核方式**：逐条读 issue 01~09 的 Comments / 遗留 / 未采纳章节，去代码里核实（不是读文档下结论）；全仓 `TODO` / `FIXME` / `HACK` / `NotImplementedError` 命中数为 **0**，无真占位。

---

## 0. 一句话结论

P0 的验收三件套（带工具的 agent loop 跑通 / checkpoint 断点续跑 / mock 单测全绿）**全部达成**（真实端点实测，见 issue 10 的 Comments）。P0 范围内被四轮往后推的「根门面导出」也一并闭环。**剩下的全部是「P1/P2 才有生产代码」的条目** —— 不是漏做，是归属没到；本文档逐条列清。

---

## 1. 本轮闭环的 P0 遗漏（备案）

| # | 遗漏项 | 出处（谁何时记的） | 本轮怎么闭环 |
|---|--------|-------------------|-------------|
| 1 | `python -m CharAgent.client` 不存在 | issue 10 本体；`DESIGN.md` 运行入口表与 `05-roadmap.md` 验收行都标着「尚未交付」 | 新建 `CharAgent/client/`（app / session / render + utils），交付 CLI 全部五条验收 |
| 2 | 根门面只导出 model + tool，缺 6 个包 | issue 05 §9 · 06 §9 · 07 §8 · 08 §5 **四处**都写「同批处理」 | `CharAgent/__init__.py` 汇聚八包共 137 个名字；加 `tests/test_root_facade.py` 防漂移（子包 `__all__` 有而根门面没有 → 红） |
| 3 | `retry/` 生产调用点为零 | issue 06 §9「接线状态（2026-09-12 复核）」 | `client/app.py::build_model` 里 `RetryingChatModel(chat_model_from_env(), policy=RetryPolicy(), on_retry=[...])` —— 正是 issue 06 指名的那一行；`--no-retry` 保留对照路径 |
| 4 | `AgentLoop` 生产调用点为零（只在测试与 docstring 里出现） | issue 06 §9 复核结论 | `client/session.py::ChatSession` 构造 loop —— 八包第一次被生产代码装配起来 |
| 5 | `.env.example` 零 `CHECKPOINT_*` / `MODELS_*` | issue 10 审计（2026-09-15） | 模板补全 9 个变量 + 注释说明三后端差别；加 `tests/test_env_template.py` 用配置模块里的常量名比对模板（防再漂）。**2026-09-15 用户要求**：这 9 个变量全部加 `CHARAGENT_` 前缀（与其他子项目的 `CHARPLOT_*` / `RK_*` / `MENU_*` 同一套命名法），其中 `MODELS_DSN` / `MODELS_ECHO` 词干一并改为 `DB_`（与 issue 08 把 `models/` 包改名 `db/` 对齐，避免与 `model/`（LLM 层）混淆） |
| 6 | `checkpoint/utils/history.py` 的 `format_history` 零生产调用点（issue 07 自己立过「不给没人调的接口」的规矩） | issue 07 §10 交付了视图但没人用 | CLI 的 `/history` 与 `--history` 用它渲染存档表 |
| 7 | `DESIGN.md` 目录结构表无 `client/` 行 | issue 10 审计 | 补 `client/` 行 + P0 产出物清单 + 快速开始加 CLI 命令块 |
| 8 | `05-roadmap.md` 的 P0 落点列全是旧单文件名（`model.py` / `tool.py` / `loop.py`） | issue 08 F3 修过 `loop/`→`agent/`，漏了 roadmap 三处 | 订正为包路径，补 P0-9（CLI）一行与最新验收现状 |
| 9 | `04-test-plan.md` 的 E2E 行承诺了不存在的东西（「REST → TaskQueue → …」全是 P1 构件） | issue 10 审计 | 拆成「E2E（P0 已有，CLI 端到端）」与「E2E（P1 规划）」，补 CLI 运行命令 |
| 10 | issue 03 / 08 的 `Status:` 字段仍是 `ready-for-agent`（bullet 全 `[x]`、Comments 写「实施完成」） | issue 10 审计 | 改为 `done` |
| 11 | `PRD.md` 的「当前状态」写「代码零实现，P0 尚未开工」 | PRD §1 | 更新为 P0 已交付并验收；框架层清单补 `client/` |
| 12 | 打断之后说「继续」不会接着跑，进度悄悄丢 | 用户复核（2026-09-15）：「能不能通过输入『继续』『刚才不小心中断任务了，继续刚才的任务』继续没完成的任务」 | 原本这句走 `_ask`，而那条提问在打断时已撤回 —— 模型只看到光秃秃一个「继续」，于是另起炉灶（用户以为在续跑，进度其实丢了）。最终做法**不是**加意图识别，而是**把上下文备齐**：失败路径上去快照把已完成的工作收回会话历史（`ChatSession._reclaim_progress`），于是「继续」就是一条普通提问，模型自己接得上。边界见 §4.7 |

**未闭环但已有明确归属的**（不算漏，只是没到）：见 §2~§4。

---

## 2. P0 → P1 交接（生产代码在 P1，接口已在 P0 备好）

按 P1 issue 编号排序。**「P0 已备好的接口」那一列是关键** —— 它说明 P1 落地时不用回头改 P0。

### 2.1 P1-1 FastAPI server（REST + SSE + TaskQueue）

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| SSE 端点与 `after_event_id` 断点续拉 | 需要 server 才有「流」可推；P0 的事件流只在进程内 | `StreamEvent.seq` 就是 `after_event_id` 的取值；`EventSink` 换成「推 asyncio.Queue」即可，loop 零改动 |
| **token 级流式**（content / reasoning 逐 token delta） | P0 的 `ChatModel.generate` 返回**完整** ModelResponse（非流式），故每次响应只产出一条完整 reasoning delta | 需给 `ChatModel.generate` 加 delta 回调 —— **这是 P1 唯一要动 P0 协议的地方**，改动面：`model/protocol.py` + 两个适配器 + `MockLLM` + `AgentLoop._decide` |
| `run_id` 注入事件 | 框架层没有 run 概念（`StreamEvent` 刻意不带 run_id） | P1 server 转发时注入，事件契约不用改 |
| 事件持久化（重启后仍能续拉） | 需要 `events` 表（P2-2）+ 存储策略 | 无（P2-2 落地后回填） |
| `/healthz` 依赖探活 | server 能力 | 各配置模块已能给连接串（`postgres_dsn` / `sqlalchemy_url` / checkpoint 的 Redis URL 回退） |
| REST 幂等（同 `request_id` 返回已有结果） | 需要 run 表 | `db.repositories.runs.get_by_request_id` 已备（issue 08 F5 补的用例点名「P1-1 接线靠它」）；`IdempotencyStore` 协议 + InMemory 实现已交付 |
| `TaskQueue`（进程内 asyncio FIFO） | server 构件（ADR-0006） | 无（P1 新建 `server/`） |

### 2.2 P1-2 运行状态机 + 取消

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 状态机的**推进者**（谁在何时把 `running` 改成 `waiting_tool` …） | issue 08 §7 明确「本 issue 交付规则与安全落库入口」 | `db/state.py` 的 `ALLOWED_TRANSITIONS` / `can_transition` / `ensure_transition` / `run_status_for_outcome`；`RunsRepository.try_transition`（带条件的 UPDATE 做乐观锁） |
| `error(cancelled)` 事件 | 框架在 `CancelledError` 传播路径上**不能 await**（无法安全发事件），故由 server 产出 | `_KillSwitch` 展示的取消语义（cancel → 收尾 → 再上报）在 CLI 已验证；`AgentLoop` 不吞 `CancelledError` |
| `POST runs/{id}/cancel` 接口 | server 能力 | 同上 —— kill switch 的机制层（`task.cancel`）在 P0 已闭环 |
| 取消后的资源释放与幂等键留痕 | 需要 run 表与幂等表（P1 迁移） | `IdempotencyStore.release` 只放行在途记录（结果不被后到的失败抹掉） |

### 2.3 P1-3 熔断 + 分层超时

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 熔断 + failover | issue 06 §8 明确「连续 429 靠三条上限兜底，熔断属 P1-3」 | `RetryPolicy` 的三个上限（次数 / 总耗时 / 单次封顶）已封死放大；`RetryAttempt` 记录单可供熔断器判断 |
| 分层超时（model 60s / tool 10-30s / run 总超时） | 需要 server 编排层 | model 侧已有单请求 timeout（适配器构造参数 `timeout=60.0`）；run 侧有 `LoopGuard` 的 wall-clock 软限制；tool 侧**没有超时**（见 §4.1） |

### 2.4 P1-4 幂等存储 + Saga + 分布式锁

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| Redis / PG 的幂等存储 + TTL | issue 06 §8「P1-4」 | `IdempotencyStore` 协议（claim / complete / release）+ `ClaimStatus` 三态已定死 |
| Saga 补偿（退款/通知） | 需要真实副作用（demo 业务） | `ClaimResult` 的 `claimed` 语义（只有 CLAIMED 才执行真实动作） |
| 分布式锁（Redis SETNX + TTL 续租 + owner 校验） | P1 新建 `lock/` | 无 |
| 幂等表迁移 | `db/README.md` 已写追加流程 | 五表已进 alembic；`0002` 起按 README 的流程追加 |

### 2.5 P1-5 限流

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 固定/滑动窗口、令牌桶/漏桶，per user / tenant / model | P1 新建 `ratelimit/` | `RATE_LIMITED` 错误码已在 `03-api.md §4` 占据位置；`db.Thread.tenant_id/user_id` 字段已建（限流键的维度来源） |

### 2.6 P1-6 安全护栏（输入/输出 + 沙箱 + SSRF）

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 四层纵深护栏、prompt injection 防护 | P1 新建 `guard/` | `content_filter` 的**终止语义**在 P0 已定：loop 层只终止循环，拦截语义归调用方（`FinishReason.CONTENT_FILTER` + `terminate_error_code` 已判它为终局 error） |
| 工具沙箱 / 最小权限（只读账号 + SQL 白名单） | 需要真实业务工具（P1-13） | `@tool` 的「一个工具一件事」写进 `tools_demo.py` 的写法约定（P1 业务工具照抄） |
| SSRF 防护 | P1 构件 | 无 |

### 2.7 P1-7 HITL + 脱敏 + 审计

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| **挂起点的触发**（哪些工具需审批） | issue 07 §8 明确「P0 交付的是能存能读能恢复的机制」 | `checkpoint.utils.types.Suspension`（reason + pending + approval_id）已可存取；`pending_tool_calls(history)` 能从 wire 形状里找出欠结果的调用；`AgentLoop.resume` 会**先补做**它们再继续（不被 guard 影响） |
| `approval_required` 事件 | 由 loop 之外的审批模块产出，不参与 P0 状态机 | `EventType` 已是可扩展枚举；`EventPrinter` 对未知事件类型有兜底分支（原样打印，不 KeyError）—— 已在 `tests/test_client_render.py` 钉住 |
| 审批表 + 超时降级（默认 15 分钟） | P1 迁移（`db/README.md` 的追加流程） | `ToolCallStatus.NEEDS_APPROVAL` 等 6 个取值已在 `db/entities.py` 定义 |
| PII 结构化脱敏、审计轨迹 | P1 新建模块 | `ToolCall` 表已含审批信息字段位；`audit_logs` 表待 P1 迁移 |

### 2.8 P1-8 降级兜底

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 模板回复 / FAQ 匹配（Redis）/ 部分结果 / 转人工 | server 层能力 | 框架只给**事实性说明**（`TERMINAL_ERROR_TEXT`），降级话术归 server —— 这条分工在 issue 05 §3 已定案；`LoopOutcome` 的六种结束原因就是 server 选降级路径的输入 |

### 2.9 P1-9 RAG（Milvus）

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 摄取 / 检索 / rerank / `final.citations` | P1 新建 `rag/` | 事件载荷是 dict（加 `citations` 键不改协议结构）；CLI 的 `EventPrinter` 对未知键不做假设 |

### 2.10 P1-10 结构化输出 / 上下文压缩

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| `response_format` / JSON schema 约束输出 | `model/__init__.py` 的「已知能力边界」已列（strict 模式 Beta / tool_choice / logprobs 等均未实现） | `_resolve_payload` 的「None 不携带」语义已定，加参数不用改架构 |
| 上下文压缩（摘要 + 截断不破坏 tool 调用结构） | issue 04 只做了 **length 截断**（CONTINUE / CONDENSE） | `TruncationStrategy` + `content_parts` 拼合链 + `max_truncations` 已跑通；摘要式压缩要新增策略成员 |
| `prefix: True` 对话前缀续写 | issue 04 §4 评估后**明确不采用**（四条理由：beta 通道 / 末条必须是 assistant 与 tool 配对冲突 / 与思考模式交互未定义 / 本意是格式引导） | 不涉及 |

### 2.11 P1-11 token 计量与预算硬上限

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 按 task 归因 + 预算硬上限 + **重试双计费的账** | issue 06 §4 明确「P0 不并入 LoopGuard 的 token 预算（避免假账），裁决属 P1-11」 | 被丢弃的用量随 `on_retry` 的 `RetryAttempt.result`（原样 ModelResponse，可读 usage）交回调用方；`retry_upstream_interrupted=False` 开关也已备（预算/缓存要「不再烧一次」时用） |
| `BUDGET_EXCEEDED` 错误码 | `03-api.md §4` 已占位（标 P2） | `LoopOutcome.TOKEN_BUDGET` 已能刹车（软限制） |

### 2.12 P1-12 结构化日志 + 指标

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| structlog JSON（request_id / trace_id / thread_id 贯穿 + 脱敏前置）、Prometheus 文本指标 | P1 新建 `logging/` | `HookPoint.ON_EVENT` / `ON_MODEL_CALL` / `ON_TOOL_EXECUTED` 五个触发点已可挂（空注册零开销，异常隔离留痕 `registry.failures`）；`retry` 的 `on_retry` 序列可挂多个观测回调 |
| token 计量打点 | 见 P1-11 | 同上 |

### 2.13 P1-13 客服 demo 业务层

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| 订单/物流/退款/FAQ/知识库/转人工六个业务工具 | 需要真实数据源与业务表 | `tools_demo.py` 是**写法模板**（Annotated + Field、可操作错误、一个工具一件事）；`query_order_status` 就是它的最小样例 |
| 订单查询走只读账号 + 独立查询层（不 import Django ORM，ADR-0004） | P1 构件 | `db/PgDatabase` 是「同步引擎 + to_thread」的样板；MySQL 只读层照同一形状写 |
| tickets / escalations / approvals / audit_logs 四表迁移 | `db/README.md` 已写追加流程 | 五表 + alembic 已就位（`0001_charagent_core`，版本表带 `charagent_` 前缀防撞） |
| 转人工接管流程 | P1 构件 | `db.conversation.visible_transcript` / `conversation_turns` 已能给出「给前端看的会话」（一问一答，不含 system/tool） |
| 退款幂等 | 见 P1-4 | 同 P1-4 |

### 2.14 P1-14 Vue 前端

| 项 | 为什么不在 P0 | P0 已备好的接口 |
|----|--------------|----------------|
| `/chat` + `/admin` 双路由，EventSource 消费 | P1 构件 | 事件契约（六类 + seq + 四条不变量）已在 P0 定稿并测试；CLI 的 `EventPrinter` 是「同一份事件流的另一种渲染」，可当渲染规则的参照 |
| Vitest 渲染测试 | P1 后期 | `tests/fixtures/snapshots/event_stream_tool_path.json` 是现成的事件序列样本 |

---

## 3. P0 → P2 交接

| 项 | 归属 | 为什么不在 P0/P1 | P0 已备好的接口 |
|----|------|----------------|----------------|
| `events` 表（事件溯源 #12） | P2-2 | issue 08 §7 明确「P2 迁移」 | 迁移体系与 `db/README.md` 的追加流程已备；`StreamEvent` 形状即表结构草案 |
| checkpoint 历史的保留与清理策略 | P2-2（#12） | issue 07 §7 明确不做 | Redis 侧已有 `CHARAGENT_CHECKPOINT_TTL_SECONDS` / `CHARAGENT_CHECKPOINT_REDIS_MAX_FRAMES`；Postgres 侧无清理（全历史是它的卖点） |
| 数据合规级联删除（向量 / checkpoint / trace） | P2-11（#28） | issue 07 §7 明确 | `PostgresCheckpointSaver.delete_thread` 已实现（Postgres 专有，不在协议里 —— 协议 docstring 已写明这条边界） |
| 连接池调优 / 无状态水平扩展 | P2-10（#66） | issue 07 初版的「一条连接 + 一把锁」已被 issue 08 的 `PgDatabase` 连接池取代；调优本身属 P2 | `PgDatabase` 支持注入引擎（`test_injected_engine_survives_dispose` 钉住 dispose 不关注入的引擎） |
| 四层记忆 / 成本追踪 / 可观测 / 多 agent / MCP / Skills / Eval / 上下文工程 | P2 的 8 个插件 | 全部走 ADR-0007 的三类扩展点挂载，**不回溯改造 P0/P1** | 事件总线（六类事件即 P2 订阅源）+ hook 注册表（五触发点，空注册零开销）+ SPI（`ChatModel` / `CheckpointSaver` / 待建的 `EmbeddingProvider` / `ModelRouter` / `SemanticCache`） |
| 语义缓存（防重复调用 LLM） | P2-4（#36） | issue 06 §8 | `retry_upstream_interrupted` 开关已备 |
| 非确定性统计（多次运行看通过率 #62） | P2-8 评估体系 | issue 09 §8 定案：默认用例全替身本身确定，没有可统计的对象 | 录制回放样本（3 份真实样本）+ `MockLLM` 三模式已就位 |
| trace 回放 / 导出轨迹文件 | P2（#59） | issue 07 §10「不导出轨迹文件（用户本轮未选）」 | 每帧已有观察值（`CheckpointMetadata`）+ 可读历史视图（`format_history`），CLI 的 `/history` 就是它的消费方 |
| Redis checkpoint 的编号索引键（免翻账本） | 未采纳（issue 07 §10） | 帧数上量后再加（LangGraph 用 JSON + RediSearch） | 现在按编号取帧是「先便宜的 JSON 解析比对编号，命中才完整解码」 |
| super-step 粒度存帧（LangGraph 那种「每节点一帧 + writes 表」） | 未采纳（issue 07 §10） | 本项目的粒度是「每 Turn 一帧 + 挂起点写在 state 里」，已够「恢复而非重跑」 | `CheckpointSource` 已能标记帧的来源（loop / fork / suspension） |

---

## 4. 「P0 就这样了」的明确边界（非遗漏，是决定）

这几条容易被后来人当成 bug 或漏做，写清楚当时的判断。

### 4.1 工具执行**没有**超时

`execute_tool` 不设超时，`AgentLoop` 也不设 —— P0 的工具集全是本地纯函数（微秒级），加超时是给不存在的场景写代码。**P1-3 落地分层超时时必须补上**：那时工具会真的打外部服务（MySQL / Milvus / 只读账号），一个慢查询就能把整个 run 挂住。落地位置：`tool/executor.py` 外层包 `asyncio.wait_for`，超时映射为**可操作错误**（回填模型，触发自纠错），而不是抛异常终止 run。

### 4.2 `RetryingChatModel` 默认**不**在 loop 里

`AgentLoop` 没有 `retry_policy=` 参数，重试只在构造 loop 的那一行以组合方式挂上（CLI 就是这么做的）。理由（issue 06 §9 复核结论，方案 A）：默认开启会让每轮 `elapsed_ms` 含重试睡眠（可能提前触发 `TIME_LIMIT`），且与 loop 现有契约注释「模型调用失败直接抛出、本层不包装不吞」冲突。P1 server 要按 run / 租户调策略时，入口一行也比 loop 参数更灵活。

### 4.3 CLI 不做多会话管理

`--thread-id` 换会话，默认固定 `cli-main`。不做「会话列表 / 切换 / 删除」—— 那是 P1 server + 前端的事（`db.repositories.threads.list_threads` 已备 `user_id` 过滤）。CLI 的定位是**框架验收演示**，不是终端版聊天产品。

### 4.4 CLI 的退出码只有三档

`0` 正常 / `1` 配置错或没答完 / `130` 被 Ctrl-C 打断。不做更细的码（如按 `LoopOutcome` 分档）—— 那会把框架的枚举值语义固化进 shell 契约，framework 加一个结束原因就要改 CLI 文档。

### 4.5 `query_order_status_manual` 不在 CLI 的工具集里

P0-2 交付了六个演示工具，CLI 只注册五个。第六个 `query_order_status_manual` 与 `query_order_status` **同能力**（同一份 mock 数据、同样输出），是 manual schema 引擎的教学对照；两个都注册会变成「同一个本事有两个工具名」，模型只能随机挑一个 —— 演示时看到哪个纯属运气。那一个留在 `tools_demo.py` 里由 `tests/test_tool_schema.py` 做双引擎对照。

### 4.6 快照表 `charagent_checkpoints` 与 `charagent_alembic_version` 的前缀是必须的

本项目各子项目**共用同一个 PG 库**。不带前缀的表名会互撞（issue 07 §9.B 实测：与 `langgraph-checkpoint-postgres` 的 `checkpoints` 撞名，报错信息与真因毫无关系）；alembic 版本表同理（issue 08 §6.A）。新增表请照 `db/schema.py` 的命名约定加 `charagent_` 前缀。

### 4.7 口语续跑（说「继续」）是**软保证**，CLI 不做任何意图识别

打断之后说一句「继续」能接着跑，机制是 `ChatSession._reclaim_progress`：失败路径上去快照把已完成的工作收回会话历史，于是历史 = 快照的完整历史 + 用户新说的话，「继续」就是**一条普通提问**，模型看着上下文自己接上。CLI 里**没有**「这句是不是续跑」的分支 —— 判断交给模型。

> 中间曾有一版用关键词启发式去认这类句子（继续/接着 + 短句或回指词 + 否定词），已删除。理由：它多一层误判面，却不如「上下文齐全」根本 —— 判错的根源不是分不清句子，而是模型手里没有上一轮做完的事。

边界写清楚：

- **「不重做」是模型自觉，不是框架保证**。工具结果就在历史里，模型没有理由重调；真实端点实测时它明确推理了「我可以再查一次? 重复调用没有意义」然后换了个有用的工具。但它**有权**重调（比如「我再确认一下」）。只读工具无所谓；P1 的副作用工具（下单 / 退款）必须靠幂等键（P1-4）兜底，不能指望模型自觉。
- **框架级的硬保证仍在 `/resume`**：模型压根不被问，起点就是快照，计数器接续、挂起点补做、`parent_id` 岔出新分支。演示与测试都还站得住（`test_interrupt_then_resume_does_not_rerun_completed_tools`）。
- **收回依赖「快照是一条直线」**：`resume` 只取 `load_latest`，不走 time-travel 分叉，所以「快照的历史」永远是「会话历史」的前缀，比长度就是比进度（`_reclaim_progress` 的「只做加法」就建立在这条上）。**P1 若接 time-travel（取老帧续跑），这里必须换成显式的版本比较**，否则会把历史按长度错裁。
- 与 §4.8 的关系：那条讲的是「中断落在工具执行中，那一轮整个丢弃」—— 收回补的是**已落盘的轮次**，救不了没落盘的那一轮。

### 4.8 「不重复已完成动作」的粒度是 **Turn**，不是每一次模型调用

快照是**每 Turn 一帧**（`_record_turn` 落盘），所以续跑重做的最小单位是「**被中断的那一轮**」—— 它本来也没跑完，重做是应该的。由此推出两种打断位置的差别：

| 打断落在 | 续跑时 | 说明 |
|---------|--------|------|
| 模型调用期间（最常见） | ✅ 无损 | 那一轮还没产出任何结果，重问一次即可 |
| **工具执行期间** | ⚠️ **那一轮整个丢弃** | 该轮的 `tool_call` 消息与工具结果都没落过盘（上一帧在那轮之前），续跑时模型**重新决策**、那个工具**可能被再调一次** |

P0 不处理这个窗口，因为演示工具是微秒级纯函数（`get_current_time` / 单位换算 / 数文本），窗口实际为零 —— 为不存在的场景写代码。**但 P1 必须处理**：P1-13 的工具会真的下单、退款、发通知，重跑一次就是事故。两条路：

- **幂等键**（P1-4 已有的 `IdempotencyKey` / `IdempotencyStore`）—— 推荐，副作用工具自己声明去重键，重跑无害；
- **更细的帧粒度**（每步一帧，LangGraph 那种 super-step，见 §3 末行「未采纳」）—— 能消掉窗口，但快照量翻倍、与现有 `parent_id` 链的语义也要重定。

落地时别只改一处：`tool/executor.py` 记录「这次执行用了哪个幂等键」→ 快照的挂起点里带上它 → `resume` 补做时先查幂等表。

---

## 5. 本轮已知的环境性事项

| 项 | 状态 | 说明 |
|----|------|------|
| 本机 Redis 未启动 | 环境 | `pytest -m redis` 3 例跳过；CLI 的 `--backend redis` 需先起 Redis。Postgres 可用（`pytest -m "pg or pg_db"` 51 passed） |
| PG 库里的遗留空表 `checkpoints` | ✅ 已不存在 | issue 07 §9.B 记录的早期测试产物（0 行）。2026-09-15 复核 `public` schema 时它已经没了 —— 应是 issue 08 §6.B「按迁移重建开发库」那一步顺带清掉的，**该项无需再处理**。当前库里只有六张 `charagent_` 前缀的表 |
| 真实端点演示的 token 消耗 | 正常 | CLI 演示一轮工具问答约 3K tokens；`pytest -m integration` 11 例约 25s |
| `python -m CharAgent.client` 要求 cwd = 仓库根 | 已知 | 无打包元数据（`pyproject.toml` 只有 `[tool.ruff]`），靠 `pytest.ini` 的 `pythonpath` 与 cwd 兜底。装包 / 入口脚本属 P2-10 工程化 |
