# SPEC: handcraft_agent — 手写 Agent Runtime + 电商售后智能客服

> Status: ready-for-agent
> Type: spec
> 来源: `project/handcraft_agent/`（README + CONTEXT + docs/design/01-05 + docs/adr/0001-0007 + docs/difficulties/01-14）
> 编制: 2026-08-18 | 关联决策: 13 轮设计访谈（已全部落盘为 ADR 与 design 文档）

---

## 1. Problem Statement

主流 Agent 框架（LangChain / LangGraph / DeepAgents）把 agent loop 封装成黑盒——开发者只配 tools + prompt，框架内部的「模型 ↔ 工具」循环、状态管理、流式推送都被藏起来了。

学习者（本项目的 owner）想**从零手写 agent loop**，把每一层暴露出来：模型调用、工具执行、结果回填、状态持久化、断点续跑、流式事件、重试/熔断/超时/幂等兜底、安全防护（注入/沙箱/HITL/脱敏/审计），并以一个**电商售后客服 demo** 自证框架可用。项目要覆盖 70 个编号难点（14 类），分 P0/P1/P2 三阶段实施，兼具学习价值与面试考点价值。

当前状态：**设计阶段已完成**（全部文档齐备），代码零实现，P0 尚未开工。

## 2. Solution

从零实现一个**业务无关的 agent runtime 框架包**（`handcraft_agent/`），在其上构建**电商售后客服 demo**（FastAPI + SSE + Vue 前端双路由），覆盖 70 个编号难点：

- **P0 框架核心**：手写 agent loop（并行工具、错误自纠错、循环防护）、ChatModel 协议（httpx 裸调 + openai SDK 双适配器）、@tool 装饰器、流式事件总线 + hook 骨架、重试退避、checkpoint 三实现、五实体数据模型 + alembic、分层测试（CLI 可演示）
- **P1 服务 + demo 闭环**：FastAPI server（REST + SSE + TaskQueue）、熔断/超时/幂等/Saga/分布式锁/限流、四层安全护栏 + HITL 审批 + 脱敏 + 审计、RAG（Milvus 检索）、结构化输出、日志指标、客服 demo 端到端（提问→检索→回复→转人工）、Vue 前端（/chat + /admin）
- **P2 可插拔生产级能力**：8 个插件模块（memory / cost / observability / multiagent / mcp / skills / eval / context_engineering），经事件总线 + hook + SPI 三类扩展点配置注册挂载，核心零 import P2（ADR-0007），不回溯改造 P0/P1

交付形态：通用框架库 + 可运行的客服 demo，每阶段有明确验收标准（见 Roadmap）。

## 3. User Stories

### 学习者（框架使用方）

1. 作为学习者，我想用 CLI（`python -m handcraft_agent.cli`）直接跑通一个带工具的问答，以便不依赖 server 就能验证 agent loop 行为。
2. 作为学习者，我想查看手写 agent loop 的完整实现（while 循环：模型决策→并行工具执行→tool_result 消息回填→事件产出），以便理解 LangGraph/DeepAgents 黑盒之下发生了什么。
3. 作为学习者，我想通过 httpx 裸调实现看清 `/chat/completions` 协议细节（tool_calls 结构 / usage / finish_reason / reasoning_content / SSE 流式），以便理解 SDK 帮我藏了什么。
4. 作为学习者，我想对比 httpx 裸调与 openai SDK 两个适配器对同一输入产生等价结果，以便评估「自研 vs SDK」的取舍。
5. 作为学习者，我想用 @tool 装饰器 + JSON schema 自动生成注册工具，以便理解框架如何把 Python 函数暴露给模型。
6. 作为学习者，我想看到 checkpoint 序列化协议（JSON + schema 版本号）与 InMemory/Redis/Postgres 三种存储实现的语义差异，以便理解「存储介质不同，checkpoint 语义也不同」。
7. 作为学习者，我想演示 checkpoint 断点续跑与 time-travel 回溯（恢复历史时刻产生新分支），以便掌握会话状态持久化的生产级做法。
8. 作为学习者，我想用 MockLLM（固定/脚本化/录制回放三模式）跑 loop 测试而不依赖真实 LLM，以便测试确定、快速、可回归。
9. 作为学习者，我想用轨迹断言（断言工具调用顺序与参数而非仅最终答案）与快照测试验证 loop 行为，以便防回归。
10. 作为学习者，我想对照 70 个编号难点清单逐个实现并打勾，以便系统性地覆盖企业级 Agent 项目的全部坑点。
11. 作为学习者，我想在面试前用「面试要点导航」快速定位各主题对应的难点与实现，以便复习。

### 客服用户（/chat 端）

12. 作为客服用户，我想在聊天窗提问（如"我的订单 20260701123456 到哪了"），以便获得即时答复。
13. 作为客服用户，我想看到 agent 的处理过程（thinking / tool_call / tool_result / reasoning 渐进渲染），以便感知系统在工作而非卡死。
14. 作为客服用户，我想看到工具失败的**可操作错误**（如"order_no 应为 14 位数字"而非 422），以便自我修正输入。
15. 作为客服用户，我想取消一次执行（kill switch），以便回答太久或思路不对时及时终止。
16. 作为客服用户，我想查询订单状态、物流轨迹、退换货政策、FAQ 等售后信息，以便自助解决问题。
17. 作为客服用户，我想提交退款申请，以便获得退款受理结果（审批通过后 1-3 个工作日原路退回）。
18. 作为客服用户，我想在退款申请挂起时知道"已转人工审核"，以便了解后续预期。
19. 作为客服用户，我想在无法自助解决时一键转人工，以便获得人工帮助。
20. 作为客服用户，我想断线重连后能拉取会话历史（GET messages），以便不丢失上下文。
21. 作为客服用户，我想重复提交同一请求时不产生重复副作用（幂等键），以便网络重试安全。
22. 作为客服用户，我想在系统降级（模型挂/检索失败）时仍得到模板回复或 FAQ 匹配，以便服务不中断。

### 人工客服（/admin 端）

23. 作为人工客服，我想在审批台看到挂起的高危操作列表（退款/赔付，含上下文与金额），以便判断是否批准。
24. 作为人工客服，我想批准或拒绝退款申请，以便挂起的 run 从断点恢复执行或回填失败原因。
25. 作为人工客服，我想在接管台查看转人工会话的完整历史，以便了解用户问题全貌。
26. 作为人工客服，我想在接管后直接回复用户（POST reply），以便继续服务。
27. 作为人工客服，我想看到审批超时（默认 15 分钟）自动拒绝的记录，以便处理遗留挂起。

### 平台/运维方

28. 作为运维方，我想通过 /healthz 探活（PG/Redis/Milvus/MySQL 依赖检查），以便快速定位故障。
29. 作为运维方，我想看到结构化日志（JSON + request_id/trace_id/thread_id 贯穿 + 脱敏前置），以便排查问题。
30. 作为运维方，我想看到 Prometheus 文本格式指标（success/retry/escalation rate、p95 latency、工具成功率、guardrail block rate），以便监控。
31. 作为运维方，我想配置限流（per user / tenant / model），以便保护模型预算。
32. 作为运维方，我想在供应商连续失败时熔断并 failover，以便系统可用性。
33. 作为运维方，我想审计每次工具调用/数据访问（audit_logs），以便合规留痕。
34. 作为运维方，我想在进程重启后从 checkpoint 恢复未完成的 run，以便容错。

### P2 能力方

35. 作为使用者，我想开启四层记忆（短期/情景/语义/程序性 + 多租户隔离），以便会话间连续性。
36. 作为使用者，我想按 task 归因成本追踪 + 预算硬上限，以便控制成本。
37. 作为使用者，我想接入 Langfuse trace / Grafana 指标告警 / Loki 日志聚合，以便完整可观测。
38. 作为使用者，我想启用多 agent 协作（Supervisor / Critic / handoff），以便复杂任务分解。
39. 作为使用者，我想通过 MCP / Agent Skills 扩展工具与知识，以便接入外部生态。
40. 作为使用者，我想用 GoldenSet + LLM-as-judge 评估 agent 质量并回归，以便持续改进。
41. 作为使用者，我想启用语义缓存防 LLM 重复调用，以便降本增效。
42. 作为使用者，我想行使数据删除权（级联删除向量/checkpoint/trace），以便合规。
43. 作为使用者，我想通过事件总线/hook/SPI 挂载任意 P2 模块而不修改核心，以便核心稳定性不受 P2 影响。

## 4. Implementation Decisions

> 全部决策来源：`docs/adr/0001-0007` + `docs/design/01-05` + 13 轮设计访谈（2026-08-18）。此处为总纲，冲突时以 ADR/design 文档为准。

### 4.1 架构分层

| 层 | 位置 | 职责 | 阶段 |
|----|------|------|------|
| 框架层 | `handcraft_agent/` | 业务无关 runtime：model / tool / loop / stream / hooks / retry / checkpoint / guard / ratelimit / lock / rag / logging | P0-P2 |
| 服务层 | `server/` | FastAPI + REST + SSE + TaskQueue，RAG 检索与业务工具注册 | P1 |
| Demo 层 | `demo/` | 电商售后客服：业务工具集 + Ticket/Escalation/Approval | P1 |
| 前端 | `ui/`（Vue 3 + EventSource） | `/chat` 用户端 + `/admin` 审批/接管台 | P1 |
| P2 插件 | `handcraft_agent/plugins/`（8 模块） | multiagent / mcp / skills / memory / cost / observability / eval / context_engineering | P2 |

### 4.2 核心协议与接口

- **ChatModel 协议**（薄）：`generate(messages, tools) -> ModelResponse`；P0 仅 DeepSeek `deepseek-v4-flash`（OpenAI 兼容），双适配器：httpx 裸调 + openai SDK，行为一致性由同一组测试约束（ADR-0001/0003）
- **ModelResponse**：文本或 ToolCall 列表；**FinishReason**：stop / tool_calls / length（触发截断处理）/ content_filter
- **Reasoning**：`reasoning_content` 流式增量推送、计入成本与窗口但**不回填历史**（#11）
- **Tool 协议**：@tool 装饰器 + JSON schema 自动生成；工具失败错误须**可操作**（#2）；工具粒度一个工具一件事（#70）
- **CheckpointSaver 协议**：InMemory（测试）/ Redis（KV 快照 + TTL，弱一致）/ Postgres（强一致 + 历史 + time-travel），配置切换（ADR-0002）
- **序列化协议**：JSON 主格式 + 自定义 encoder/decoder（datetime/嵌套 dict）+ schema_version 向前兼容迁移（#5）
- **EmbeddingProvider SPI**：CloseAI `text-embedding-3-large`（dim=1536）
- **ModelRouter / SemanticCache SPI**：P1 默认直连/不启用，P2 提供实现（#36）

### 4.3 Agent Loop 语义

- while 循环：模型决策 → 并行执行工具（gather + return_exceptions）→ 以 tool_result 消息回填（保持并行语义）→ 事件产出
- 错误自纠错：可操作错误回填模型二次调用；失败无解则走降级
- LoopGuard：max_turns / token 预算 / wall-clock + kill switch（asyncio.Task.cancel 即时打断）
- 每 Turn 结束 checkpoint 落盘（含 HITL 挂起点，审批后从断点恢复而非重跑）
- 上下文压缩：#6/#7，摘要 + 截断不破坏 tool 调用结构（P1）

### 4.4 数据模型（五实体，P0 一次定死）

Thread（thread_id/tenant_id/user_id/title/status/时间戳）→ Run（run_id/thread_id/status 状态机/request_id 幂等键/model/prompt_version/tokens/cost/turn_count/error）→ Message（message_id/thread_id/run_id/role/content/reasoning 存而不回填/tool_call_ids/hidden）→ ToolCall（tool_call_id/run_id/tool_name/arguments/status 含 needs_approval/result/duration_ms/审批信息）→ Checkpoint（checkpoint_id/thread_id/run_id/turn_number/schema_version/state/parent_id 分支来源）。

- Postgres DDL：核心五表 + demo 业务表（tickets / escalations / approvals / audit_logs）+ 幂等表（idempotency_keys），**alembic 统一管理**（#5/#12）
- event 表（事件溯源）P2 追加，不回溯改造（#12）
- 存储分布：checkpoint 历史 + demo 表 → Postgres；checkpoint 快照 → Redis；订单 → MySQL 只读（独立查询层直连，**不 import Django ORM**，只读账号最小权限，演示 #24）；向量 → Milvus

### 4.5 Milvus 设计

- Collection `support_knowledge`（本机 Docker，database: charlotte）
- HNSW（M=16, efConstruction=200），metric=COSINE，dim=1536
- 标量字段：doc_id / chunk_id / tenant_id / user_id / source / category / created_at
- 检索强制 tenant_id 过滤（#32）；父子索引（父块喂模型）；引用溯源（#29）
- 摄取：pypdf → 清洗 → 元数据 → chunk（size≈500, overlap≈50）→ upsert；粗召回 top_k=20 → rerank（P2 SPI）→ top-5 喂模型

### 4.6 Server API 与事件协议

- REST 端点：POST /threads、GET /threads/{id}/messages、POST /threads/{id}/runs（长任务 HTTP 立即返回）、GET runs/events（SSE，after_event_id 续拉）、POST runs/{id}/cancel、GET /approvals、POST approvals/{id}/approve|reject、POST escalate、POST reply、GET /tickets、GET /healthz
- 幂等：所有 POST 接受 request_id（#13/#17），重复返回已有结果
- SSE 事件：thinking / tool_call / tool_result（error 可操作）/ approval_required / reasoning（增量不入历史）/ final（含 citations/tokens/cost）/ error（code + message）；每事件带 seq
- 错误码：LLM_DOWN / LLM_TIMEOUT / RAG_DOWN / TOOL_ERROR / RATE_LIMITED / CANCELLED / BUDGET_EXCEEDED（P2），各配降级路径（#19）
- 前端：单 Vue 项目双路由（/chat + /admin），EventSource 消费，事件渐进渲染

### 4.7 可靠性 / 安全 / HITL

- TaskQueue：进程内 asyncio FIFO + 并发状态锁，抽象预留分布式 MQ（ADR-0006）
- 重试：指数退避 + jitter，只对瞬态错误（429/5xx/超时），4xx 放弃；注意重试重复计费 token（#13）
- 熔断 + failover + 分层超时（model 60s / tool 10-30s / run 总超时）（#14/#15）
- 幂等 + Saga 补偿（退款/通知场景）、分布式锁（Redis SETNX + TTL 续租 + owner 校验）（#17/#21）
- 限流：固定/滑动窗口 + 令牌桶/漏桶，per user / tenant / model（#22）
- 安全：四层纵深护栏（输入/输出）、Prompt Injection 防护（#23）、工具沙箱/最小权限（只读账号 + SQL 白名单 #24）、SSRF（#30）、输出护栏 + 引用溯源（#29）
- HITL：高危操作挂起持久化 → 审批恢复/拒绝回填/超时降级（默认 15 分钟）（#25）；PII 结构化脱敏（#26）；审计轨迹（#27）
- 降级兜底：模板 / FAQ 匹配（Redis）/ workflow 路径 / 部分结果 / 转人工（#19）
- 可观测：structlog JSON + request_id/trace_id/thread_id + 脱敏前置；Prometheus 文本指标最小集（#38/#39，P1）；Langfuse/Grafana/Loki P2 插件化

### 4.8 扩展点（ADR-0007，P2 不回溯）

- 事件总线：StreamEvent 四类事件即 P0 流式通道，P2 订阅同一事件流
- hook 注册表（P0 骨架落地，空注册零成本）：before_turn / after_turn / on_model_call / on_tool_executed / on_event
- SPI：ChatModel / CheckpointSaver / EmbeddingProvider / ModelRouter / SemanticCache 可替换
- 挂载：配置 `PLUGINS={...}` + 惰性 import；依赖方向 P2 → 核心单向，核心零 import P2

### 4.9 阶段任务序列（Roadmap）

- P0（8 任务）：model.py → tool.py → loop.py（并行/纠错/防护）→ stream.py+hooks.py → retry.py → checkpoint/ → models.py+alembic → tests/
- P1（14 任务）：server（SSE+TaskQueue）→ 状态机/取消 → 熔断/超时 → 幂等/Saga/锁 → 限流 → 安全护栏 → HITL/脱敏/审计 → 降级 → RAG → 结构化输出/压缩 → token 计量 → 日志指标 → 客服 demo → 前端
- P2（11 任务）：8 个插件模块 + event 表 + 规划文档 + 工程化（全部独立挂载）
- 依赖顺序：P0-1/P0-2 并行 → P0-3 → P0-4/5/6 并行 → P0-7 依赖 P0-6；P1-1 依赖 P0 全量

## 5. Testing Decisions

> 原则：测试测「代码对不对」（确定性，可 mock）；评估测「输出好不好」（非确定性，LLM-as-judge，P2）。分层 + 默认 mock + 集成测试开关（RUN_INTEGRATION=1）。

### 5.1 测试切入点（Seams）

测试不依赖实现细节，全部经现有设计内建的协议 seam 注入 mock：

| Seam | 位置 | 注入方式 | 用途 |
|------|------|---------|------|
| **Seam 1（最高）: ChatModel 协议** | `model.py` 的 ChatModel 接口 | MockLLM 三模式实现同一协议（固定返回 / 脚本化序列 / 录制回放），**被测代码零改动** | 全部 loop / server 测试的模型侧隔离 |
| **Seam 2: CheckpointSaver 协议** | `checkpoint/base.py` | InMemory 实现用于单测；Redis/Postgres 双实现语义对比测试 | checkpoint 行为测试 + time-travel |
| **Seam 3: Tool 注册边界** | @tool 装饰器 | 测试注册 mock 工具 + 直接调用工具函数 | tool schema 生成、错误语义测试 |
| **Seam 4: REST/SSE 端点** | FastAPI app | httpx TestClient + MockLLM + 打桩外部服务 | E2E：事件序列断言 |

E2E 的 SSE 事件序列断言是核心验收 seam：`thinking → tool_call → tool_result → final` 快照对比（#4/#63）。

### 5.2 分层测试策略

| 层 | 工具 | 内容 | 默认执行 |
|----|------|------|---------|
| 单元 | pytest + pytest-asyncio | model 解析、tool schema、loop 分支、序列化协议、限流算法、锁 | ✅ 全 mock |
| 集成 | pytest + respx | httpx 裸调 vs openai SDK 行为一致（同组测试约束 ADR-0003）；checkpoint 双实现语义对比；SSE 事件流组装 | ✅ 全 mock |
| 真实集成 | pytest（`RUN_INTEGRATION=1`） | 真实 DeepSeek 协议字段正确性、流式 delta、reasoning_content 分离 | 🔶 开关 |
| E2E | pytest + httpx | REST → TaskQueue → loop → 工具 → SSE 事件序列断言 | ✅ mock + 桩 |
| 前端 | Vitest（可选） | EventSource 事件渲染 | P1 后期 |

### 5.3 核心测试矩阵

单工具调用 / 并行工具（验证并发执行、部分失败回填）/ 错误自纠错（报错→回填→二次调用成功）/ 循环防护三种触发点 / 断点续跑 + time-travel / reasoning 不回填 / 事件序列快照 / HITL 全流程（挂起→批准恢复不重跑→拒绝回填→超时降级）/ 幂等 / 取消 / 降级路径 / 限流边界 / checkpoint 双实现一致性。

确定性：temperature=0 + seed 固定 + 注入随机源（#61）。

### 5.4 先例

- 仓库内现有测试先例：`app/minimall/tests/`（test_api / test_models / test_services，DRF 分层测试）——本项目采用同款分层风格但技术栈不同（pytest-asyncio + respx）
- Mock 外部服务、不 Mock 自己的代码；AAA 模式；测试名称描述行为（系统级规范 §5）

## 6. Out of Scope

- **P2 具体实现**：spec 只定扩展点与挂载机制，8 个插件模块的内容实现属 P2 各任务范围
- **demo/ 目录**（根仓库自学教程）：非业务代码，本项目不涉及
- **minimall 业务改动**：仅复用其订单数据语义，以只读查询层访问，不 import Django ORM、不改 minimall 代码
- **真实多实例部署 / 分布式 MQ**：TaskQueue 抽象预留，P1 仅进程内实现
- **WebSocket**：明确否决（ADR-0005），双向交互走独立 REST 端点
- **接入 LangChain / LangGraph / DeepAgents 实现框架**：本项目为手写实现，仅文档对比（#57）
- **多模态 / 代码解释器 / A2A**（#52-54）：P2 能力扩展范畴
- **Sentry 自托管**：排除，仅 P2 可选项对接
- **前端 UI 细节**：只定路由与承载能力，不做视觉规范

## 7. Further Notes

- **前置服务**（本机已具备）：Postgres（本机）、Redis（Docker）、Milvus（Docker）、MySQL（minimall 库，只读账号）；`.env` 按 `.env.example` 配置
- **依赖**：已登记 requirements.txt（新增 alembic / pytest / pytest-asyncio / respx）；Python 3.13 + 根 .venv 复用
- **启动入口**：P0 `python -m handcraft_agent.cli`；P1 `python -m server.main` + `npm run dev`
- **测试运行**：`pytest tests/`（默认全 mock）；`RUN_INTEGRATION=1 pytest tests/integration/`；checkpoint Postgres 测试需本机 PG
- **文档体系**：README（总览+难点索引）/ CONTEXT（术语表）/ docs/design/（01 架构、02 数据模型、03 API 协议、04 测试计划、05 路线图）/ docs/adr/（0001-0007）/ docs/difficulties/（14 类 70 编号）
- **实施起点**：P0-1（model.py ChatModel 协议 + 双适配器 + reasoning 兼容分支）→ P0-2（tool.py）并行，依赖顺序见 05-roadmap.md §依赖顺序要点
