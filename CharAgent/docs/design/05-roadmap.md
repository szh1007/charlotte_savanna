# 05 路线图（P0 → P1 → P2）

> 难点编号对应 DESIGN.md「难点清单」；阶段划分与 DESIGN.md「分阶段计划」一致。P2 全部模块走插件化挂载（ADR-0007），不回溯改造 P0/P1。

## P0：核心 loop + 可靠性 + 数据层 + CLI

目标：带工具的 agent loop 跑通；checkpoint 可断点续跑；mock 单测通过。**交付物：框架包可用（CLI 可演示）。**

| # | 任务 | 落点文件 | 难点 |
|---|------|---------|------|
| P0-1 | `ChatModel` 协议 + httpx 裸调 / openai SDK 双适配器（DeepSeek `deepseek-flash`，含 reasoning_content 兼容分支）✅ | `model/`（protocol / client_httpx / client_sdk / parse / stream + utils） | #11, #68, ADR-0001/0003 |
| P0-2 | `@tool` 装饰器 + JSON schema 生成（参数设计、描述规范）✅ | `tool/`（decorator / executor / schema + utils） | #10, #70, #68 |
| P0-3 | 手写 agent loop：并行工具（gather + return_exceptions）、错误自纠错（可操作错误回填）、循环防护（max_turns / token 预算 / wall-clock / kill switch）、finish_reason 处理（length 截断）✅ | `agent/`（loop / guard + utils） | #1, #2, #3, #10, #65 |
| P0-4 | 流式事件状态机（六类事件：四类主事件 + reasoning 旁路 + error 终局），事件总线 + hook 注册表骨架（空实现）✅ | `stream/` + `hooks/` | #4, #11, ADR-0007 |
| P0-5 | 重试 + 指数退避 + jitter + 幂等键（只对瞬态错误重试，4xx 放弃；重试在模型调用层包装 ChatModel，loop 零改动）✅ | `retry/`（policy / executor / chat_model / idempotency） | #13 |
| P0-6 | Checkpoint：序列化协议（JSON + 自定义序列化器 + schema 版本号）、InMemory / Redis / Postgres 三实现、time-travel 分支 ✅ | `checkpoint/`（base / memory / redis / postgres / serialization / config + utils） | #5, ADR-0002 |
| P0-7 | 五实体数据模型（thread/run/message/tool_call/checkpoint）定义 + Postgres 表 + alembic 初始化 ✅（顺带把 P0-6 的裸 psycopg 实现统一到 SQLAlchemy，表定义收敛到 `db/schema.py` 一处） | `db/`（schema/entities/state/conversation/database/config/errors + repositories/）+ `alembic/` | #5, #12(实体部分) |
| P0-8 | 分层测试 + MockLLM（固定/脚本化/录制回放）+ 轨迹断言 + 快照测试 ✅ | `tests/` | #61, #62, #63 |
| P0-9 | CLI 入口（P0 验收线）：带工具问答端到端 + 六类事件终端展示 + Ctrl-C 打断后 `/resume` 续跑 + 快照后端三选一；顺带把 `retry/` 接上生产调用点并补齐根门面导出 ✅ | `client/`（app / session / render + utils） | #3, #5, ADR-0002 |

验收（P0 整体验收线，归 issue 10）：CLI `python -m CharAgent.client` 跑通带工具问答；checkpoint 中断续跑演示；`pytest tests/` 全绿。
> 现状（2026-09-15，issue 10 交付后）：**三项全部达成**。真实端点实测：带工具问答（含并行双工具）通过、`[tool_call]` 全程仅一次的中断续跑通过、`--backend` 三后端切换通过；`pytest tests/` 710 passed。
> P0 阶段遗留（不阻断验收、按归属推给后续阶段）见 `.scratch/CharAgent/P0-to-P1-P2.md`。

## P1：可靠性加固 + 安全 + RAG（业务 demo 已外移）

目标：SSE 流式；状态机/取消；熔断超时；幂等/Saga/锁；护栏；HITL 审批；审计；降级；RAG 全部可测。**交付物：框架侧通用运行时 HTTP 层（router 工厂）+ 各能力模块。**

> **2026-09-18 变更**：原「客服 demo + 前端」两项**整个移交仓库根的新子项目 `CharService/`**（ADR-0008 分层剥离 —— 业务 UI 与业务工具不属于业务无关的框架包）。业务端到端验收由 `.scratch/CharService/issues/01~10` 承担。本表 P1-1/5/6/7/13/14 已按 ADR-0008/0009 修订。

| # | 任务 | 落点文件 | 难点 |
|---|------|---------|------|
| P1-1 | FastAPI **通用运行时**层：REST + SSE + TaskQueue（进程内 asyncio FIFO + 并发状态锁）+ 取消 + 长任务异步化；**以 router 工厂交付**，业务端点归 `CharService/`；会话创建支持绑定 `user_id`/`tenant_id` | `server/` | #20, ADR-0006 |
| P1-2 | 运行状态机 + 流式中断/取消（asyncio cancel 语义 + 资源释放 + 幂等键留痕）；状态机规则与落库入口见 `db/state.py` + `repositories/runs.py` | `agent/` + `server/` | #16, #18, #65 |
| P1-3 | 熔断 + failover、分层超时（model 60s / tool 10-30s / run 总超时）、工具取消（**工具侧超时 P0 没写**，见 `design/06-boundaries.md` §6.1）；另增**通用下游依赖熔断**（作用于被配置的依赖，上报通用码 `DEPENDENCY_DOWN`） | 新建 `circuit.py`（`agent/guard.py` 是 LoopGuard 的软限制，不是熔断器） | #14, #15 |
| P1-4 | 幂等 + **Saga 原语**（正向链 + 逆序补偿 + 补偿幂等，用假三步流程测试）、分布式锁（Redis SETNX + TTL 续租 + owner 校验）；`IdempotencyStore` 协议与 InMemory 实现已在 P0 交付 | `retry/` + 新建 `lock/` | #17, #21 |
| P1-5 | 限流（固定/滑动窗口 + 令牌桶/漏桶，per user / tenant / model **+ per tool**）；**部署位置在网关侧**（ADR-0008：agent 进程内可被绕过） | 新建 `ratelimit/`（调用方是 `CharService/gateway/`） | #22, #68 |
| P1-6 | 安全：输入/输出护栏（四层纵深）、Prompt Injection 防护、工具沙箱/最小权限（**出站目标白名单 + 参数校验**，ADR-0008）、**越权身份不可构造**（`user_id` 不在工具签名，ADR-0009）、SSRF 防护、输出护栏 + 引用溯源 | `guard.py` | #23, #24, #29, #30 |
| P1-7 | HITL **机制层**（挂起持久化 → 恢复 / 拒绝回填 / 超时降级）、**两条挂起路径不耦合**（内部审批 / 用户确认）、挂起记录带 initiator、审批鉴权可注入（默认放行）、PII 脱敏、审计**工具调用粒度**。**策略移出**：金额分层 / 角色名单 / `confirm_token` 校验归 `CharService/` | `guard.py` + `server/` | #25, #26, #27 |
| P1-8 | 降级**分类与映射层**：错误码语义边界 + `LoopOutcome → code` 映射 + `TERMINAL_ERROR_TEXT` + 降级策略 SPI。**出口移出**：模板 / FAQ / 转人工建议归 `CharService/` | `server/` | #19 |
| P1-9 | RAG：文档摄取（pypdf + 清洗 + 元数据 + chunk）、Milvus 检索（metadata 过滤 + 引用溯源）、语义缓存 SPI 预留 | `rag/` | #45, #46, #47 |
| P1-10 | 结构化输出（response_format + json_schema + 校验重试）、上下文压缩（摘要 + 截断，不破坏 tool 结构；P0 已有 length 截断的 CONTINUE / CONDENSE 两条路径） | `agent/` + `model/` | #6, #7 |
| P1-11 | Token 计量（请求前预估 + usage 回填 + 预算挂钩；**重试双计费的账**：被丢弃的用量在 `RetryAttempt.result` 里） | `retry/` / `model/` | #35 |
| P1-12 | 结构化日志（structlog JSON + request_id/trace_id/thread_id 贯穿 + 脱敏前置）+ 指标最小集（Prometheus 文本格式） | 新建 `logging/` + 核心层配置（observability 插件属 P2） | #38, #39 |
| P1-13 | ~~客服 demo：业务工具集、Ticket/Escalation/Approval 表、转人工接管流程~~ **已整体移交 `CharService/`**（issues 01~10） | ~~`demo/`~~ → `CharService/` | #19, #25 |
| P1-14 | ~~前端：Vue 3 + EventSource 双路由~~ **已整体移交 `CharService/frontend/`**（并扩展确认卡片与审批台角色区分） | ~~`ui/`~~ → `CharService/frontend/` | #4 |
| P1-15 | **运行时上下文注入 + 挂起信号通道**（ADR-0010）：`Injected()` 标记 + `ToolContext` + schema 跳过 + `execute_tool(context=)` + `run(context=)`；工具返回 `Suspension` → loop 落帧停下；状态机补「等待内部审批」态；`RunsRepository.list_by_status` | `tool/` + `agent/` + `db/` | — |

验收（框架侧）：SSE 流式输出；状态机/取消（含两种等待态）；熔断超时；幂等/Saga 原语/锁；护栏（含越权身份不可构造）；**运行时上下文注入与挂起信号通道**（ADR-0010）；HITL 四条路径（恢复 / 拒绝 / 超时 / 用户确认）；审计**工具调用粒度**；降级**分类与映射**。
> 业务端到端验收（客服 demo 跑通 / 退款审批全流程 / 转人工接管）改由 `.scratch/CharService/PRD.md` §4.8 的 10 个切片承担。

## P2：记忆 / 成本 / 可观测 / 多 agent / 能力扩展 / 评估 / 工程化（插件化）

目标：完整生产级能力。**全部经配置注册挂载（ADR-0007），不回溯改造 P0/P1。**

| # | 任务 | 落点 | 难点 |
|---|------|------|------|
| P2-1 | 上下文工程：窗口管理、动态组装、工具结果截断、lost-in-the-middle、prompt 缓存；意图识别 + 澄清 | plugins/context_engineering | #8, #9 |
| P2-2 | 数据模型补全：event 表（事件溯源）、TTL 清理 | db + alembic 迁移 | #12 |
| P2-3 | 记忆系统：四层记忆、多租户隔离、写入/检索/遗忘机制 | plugins/memory | #31, #32, #33 |
| P2-4 | 成本：成本追踪（task 归因 + 预算硬上限 + 告警）、模型分级路由、语义缓存（防穿透/击穿/雪崩）、Batch API | plugins/cost | #34, #36, #37 |
| P2-5 | 可观测补全：Langfuse trace（compose 自托管）、指标告警（Grafana）、版本化（prompt/model/tool）、Loki 日志聚合 | plugins/observability | #39, #40, #41 |
| P2-6 | 多 agent：Supervisor / P2P、Critic、handoff 循环检测 | plugins/multiagent | #42, #43, #44 |
| P2-7 | 能力扩展：MCP（client + server）、Agent Skills（渐进式披露 + 分层路由）、多模态、代码解释器、A2A | plugins/mcp / skills | #50-54 |
| P2-8 | 评估：Agent 评估体系（LLM-as-judge + GoldenSet）、trace 回放调试、数据飞轮；RAG 检索评估（hit rate / MRR / faithfulness） | plugins/eval + rag/eval | #48, #49, #58, #59, #60 |
| P2-9 | 架构与策略：Planning 范式（ReAct / Plan-and-Execute / Reflection / Replan）、Workflow vs Agent、框架对比文档 | docs + plugins | #55, #56, #57 |
| P2-10 | 工程化：无状态化水平扩展、优雅停机 drain、延迟优化、连接池/配置/异常工程底座 | server + docs | #64, #66, #67 |
| P2-11 | 数据合规删除权（GDPR 被遗忘权：级联删除向量/checkpoint/trace） | plugins/observability | #28 |

验收：多租户隔离、成本追踪、指标告警、多 agent 协作、评估回归、无状态水平扩展。

## 依赖顺序要点

- P0-1/P0-2 并行起步 → P0-3（loop）依赖前两者 → P0-4/P0-5/P0-6 并行 → P0-7 依赖 P0-6 → P0-8 贯穿全程 → P0-9（CLI）依赖 P0-4/P0-5/P0-7/P0-8
- **P1-15 依赖 P0**（其 `Blocked by` 是 P0 验收 CLI）→ **P1-1 依赖 P0 全量 + P1-15**（列挂起 / 恢复 / `transcript` 三个端点都要接它）→ P1-2 依赖 P1-1 → P1-6/P1-7 依赖 P1-2（状态机 + 取消），**P1-7 另依赖 P1-15**；P1-3/P1-4/P1-5/P1-8/P1-9/P1-12 依赖 P1-1
- 业务侧（`CharService/`）的依赖见 `.scratch/CharService/issues/01~10`：最小前置集是 **P1-1 / P1-15 / P1-7 / P1-4 / P1-5 / P1-9**，咽喉链 **P1-15 → P1-1 → CharService 01**
- P2 全部模块独立于 P0/P1 验收，按需逐个挂载
