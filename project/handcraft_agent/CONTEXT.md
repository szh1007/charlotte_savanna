# handcraft_agent

一个从零手写的轻量 AI Agent 运行时框架 + 智能客服 demo，用于吃透 Agent 底层原理（工具调用循环、状态管理、流式、安全、成本、可观测）

本文档是领域术语表（glossary），统一命名，消除歧义。

## 核心概念关系

AgentRuntime 驱动 Agent，Agent 通过 Tool 完成任务。每个 Turn 里，模型（ChatModel）产出 ModelResponse：若含 ToolCall，runtime 并发执行对应 Tool 并回填 ToolResult；会话状态按 Thread 分区，经 CheckpointSaver 落 Checkpoint（含 Serialization 序列化协议）实现断点续跑；整个过程产出 StreamEvent 推给前端，并记录为 Trace 供评估（GoldenSet + Judge）。

## Language

### 框架核心

**Agent**:
由模型驱动的自治执行体，通过循环调用工具完成任务。
_Avoid_: Bot, 机器人

**AgentRuntime**:
驱动 Agent 运行的引擎，承载「模型 ↔ 工具」循环、状态管理和流式输出。
_Avoid_: 框架, 编排器

**Tool**:
可被 Agent 调用的能力封装，含名称、描述和 JSON schema。
_Avoid_: 函数, 插件

**ToolCall**:
模型发起的一次工具调用请求，含结构化参数。
_Avoid_: function call

**ToolResult**:
工具执行后的返回，回填给模型继续推理。
_Avoid_: 返回值

**Turn**:
Agent 循环的一次迭代：模型决策 → 工具执行 → 结果回填。
_Avoid_: 轮次, 步骤

**Thread**:
一次会话的持久化标识，用于隔离不同会话的状态与上下文。
_Avoid_: session, 会话

**Checkpoint**:
某时刻会话状态的快照，支持断点续跑和时间回溯。
_Avoid_: 存档, 快照

**CheckpointSaver**:
Checkpoint 的存储接口抽象，隔离存储介质（内存/Redis/Postgres）。
_Avoid_: 存储层

**Serialization**:
Checkpoint 的序列化协议（pickle / JSON / msgpack），需处理不可序列化对象与向前兼容（schema 版本号 + 迁移）。
_Avoid_: 序列化格式, 持久化格式

**ChatModel**:
模型接入的协议抽象，隔离具体模型与 HTTP 调用方式。
_Avoid_: LLM, 模型客户端

**ModelResponse**:
模型的一次响应，含文本或 ToolCall 列表。
_Avoid_: 响应, 输出

**FinishReason**:
模型响应的终止原因，含 stop（结束）/ tool_calls（继续调工具）/ length（token 截断）/ content_filter（被拦截），决定 loop 继续还是结束。
_Avoid_: 结束标记, 停止原因

**Reasoning**:
推理模型的思维链（reasoning_content），流式增量输出、计入成本与窗口但不回填历史，模型层需兼容有无该字段。
_Avoid_: 思考过程, CoT, 思维链

**StreamEvent**:
流式输出的最小事件单元，含 thinking / tool_call / tool_result / final 四类。
_Avoid_: chunk, 事件

**LoopGuard**:
循环防护，通过 max_turns、token 预算、wall-clock 时间与 kill switch 防止 Agent 无限循环。
_Avoid_: 循环限制

**ContextCompaction**:
上下文压缩，对多轮膨胀的历史做摘要/截断，且不破坏 tool 调用结构。
_Avoid_: 历史裁剪

**StructuredOutput**:
强制模型按 JSON schema 输出的机制，校验失败则重试。
_Avoid_: JSON 模式

**IntentClarification**:
意图澄清，当用户表达不清或指代模糊时反问确认。
_Avoid_: 澄清反问

**Trace**:
一次 Agent 运行的可观测记录，含每步的 Tool 调用、token、耗时。
_Avoid_: 日志, log

**GoldenSet**:
评估用的问题-期望答案/期望工具调用集合。
_Avoid_: 测试集

**Judge**:
用 LLM 对 Agent 输出打分的评估器。
_Avoid_: 评分器

**Subagent**:
被主 Agent 委托执行子任务的子 Agent。
_Avoid_: 子智能体

**Handoff**:
主 Agent 将任务移交给 Subagent 的交接动作。
_Avoid_: 转交, 委托

### 可靠性

**RetryPolicy**:
重试策略，只对瞬态错误（429/5xx/超时）做指数退避 + jitter 重试，不重试 4xx；注意重试会重复计费 token，需缓存响应或挂钩预算。
_Avoid_: 重试

**CircuitBreaker**:
熔断器，供应商连续失败时进入半开/全开/关闭状态，自动 failover 到备份模型。
_Avoid_: 降级开关

**Timeout**:
分层超时（model / tool / whole run），卡死的工具可被取消。
_Avoid_: 超时限制

**RunState**:
运行状态机（created/running/waiting_tool/waiting_user/failed/finished/cancelled），由执行层推进而非模型建议。
_Avoid_: 运行状态

**IdempotencyKey**:
幂等键，真实动作（下单/退款/发通知）防止重复执行。
_Avoid_: 去重键

**Saga**:
分布式补偿事务，正向执行 + 失败逆序补偿，保证真实副作用可回滚。
_Avoid_: 补偿事务

**Cancellation**:
流式中断/取消，用户停止或断连时优雅终止并释放资源，已执行副作用走补偿。
_Avoid_: 中断

**Degradation**:
业务降级兜底，模型挂 / RAG 失败 / 超时等场景回退到模板、FAQ、转人工。
_Avoid_: 降级

**TaskQueue**:
并发请求排队 + 异步任务队列，长任务 HTTP 立即返回，后台跑 + SSE/轮询拉进度。
_Avoid_: 队列

**DistributedLock**:
分布式锁，多实例下跨进程互斥（Redis SETNX + TTL 续租 + owner 校验），防止惊群。
_Avoid_: 全局锁, 跨进程锁

**RateLimiter**:
限流器，固定窗口 / 滑动窗口 / 令牌桶 / 漏桶，per user / tenant / model 多级限流。
_Avoid_: 流量控制, 限流

### 安全

**PromptInjection**:
提示注入，不可信输入 + 私有数据 + 对外通信的「致命三合一」，用四层护栏纵深防御。
_Avoid_: 越狱

**Sandbox**:
工具沙箱/最小权限，只读账号、SQL/命令白名单、参数校验防注入与路径穿越。
_Avoid_: 隔离环境

**HITL**:
Human-in-the-Loop 人工审批，高危操作（转账/删数据/发邮件）需人工确认；挂起点要持久化，审批后从断点恢复执行。
_Avoid_: 人工介入

**Redaction**:
敏感信息脱敏，PII 与 API Key 在日志/trace 中结构化脱敏。
_Avoid_: 打码

**AuditTrail**:
审计轨迹，每次工具调用/数据访问可回溯，供合规重构推理路径。
_Avoid_: 审计日志

**DataDeletion**:
数据合规删除权（GDPR 被遗忘权），用户要求删除时物理删除数据并级联到衍生数据（向量/checkpoint/trace），而非仅软删除。
_Avoid_: 数据清除

### 记忆

**Memory**:
记忆，分短期/情景/语义/程序性四层，冲突用时间衰减解决。
_Avoid_: 上下文记忆

**Tenant**:
租户，用 tenant_id/user_id 隔离数据与记忆，检索强制过滤防跨租户泄露。
_Avoid_: 用户隔离

**RecencyWeighting**:
时间衰减权重，新记忆权重高于旧记忆，解决记忆冲突。
_Avoid_: 新鲜度权重

### 成本

**CostTracking**:
成本追踪，按 task（而非 token）归因，支持预算硬上限与告警。
_Avoid_: 计费

**TokenMetering**:
Token 计量，tiktoken / BPE 原理，不同模型 tokenizer 差异；请求前预估做预算截断与成本核算。
_Avoid_: token 统计, token 计算

**ModelRouter**:
模型分级路由，简单步骤用 cheap 模型、难推理用 strong 模型。
_Avoid_: 模型分流

**SemanticCache**:
语义缓存，高频相似问题命中缓存，减少重复 LLM 调用；需防穿透/击穿/雪崩三大风险。
_Avoid_: 相似缓存

**BatchAPI**:
批量异步 API，打包请求降成本（约 50%），以延迟换成本，适合离线评估与批量标注。
_Avoid_: 批量调用

### 可观测

**StructuredLogging**:
结构化日志，JSON 格式 + request_id / trace_id / thread_id 贯穿，脱敏前置，接入日志平台。
_Avoid_: 日志格式

**Metric**:
指标，success/retry/escalation rate、p95 latency、工具成功率、guardrail block rate。
_Avoid_: 监控指标

**Versioning**:
版本化，prompt/model/tool 版本随 trace 记录，支持灰度与回滚。
_Avoid_: 版本管理

### 多 agent

**Supervisor**:
监督者拓扑，主 Agent 调度多个 Subagent，与 P2P 对等拓扑相对。
_Avoid_: 主从

**Blackboard**:
共享黑板，多 agent 通过共享状态协作，与私有记忆相对。
_Avoid_: 共享状态

**Critic**:
评审 agent，对主 agent 输出做独立评审/挑错。
_Avoid_: 评审器

### RAG

**KnowledgeInjection**:
知识注入选型，RAG（更新快/可溯源/便宜）vs 微调（固化行为/降推理成本）vs 长上下文（跨文档推理）的组合取舍。
_Avoid_: 知识接入

**Ingestion**:
RAG 数据摄取，文档解析（PDF/Word/表格/OCR）+ 清洗去重 + 元数据提取，摄取质量决定检索上限。
_Avoid_: 数据导入

**Chunk**:
分块，按 size/overlap 切分文档，支持父子索引。
_Avoid_: 切片

**Embedding**:
向量化，将文本转为向量，选型影响检索质量；模型升级后向量空间不兼容需 re-embedding。
_Avoid_: 文本编码

**HybridSearch**:
混合检索，BM25 关键词 + 向量语义加权融合。
_Avoid_: 混合召回

**Rerank**:
重排，对粗召回结果用重排模型精排。
_Avoid_: 精排

**QueryRewrite**:
查询改写，检索前改写/扩展用户问题以提升召回。
_Avoid_: 查询扩展

**RetrievalEval**:
检索评估，命中率/MRR/忠实度/幻觉率，三层排查检索不到/检索错/生成错。
_Avoid_: RAG 评估

### 能力扩展

**MCP**:
Model Context Protocol，连接外部工具 server 的标准协议。含三类能力（tools / resources / prompts）与三种传输（stdio / SSE / Streamable HTTP）。
_Avoid_: 工具协议

**Skill**:
Agent Skill，渐进式披露加载的指令+脚本+模板+示例封装，与 MCP 互补（MCP 提供连接，Skill 提供知识）。
_Avoid_: 技能插件

**SkillRouter**:
技能路由，skill 多时用规则/语义/模型分层选择正确的 skill。
_Avoid_: 技能选择

**A2A**:
Agent-to-Agent 协议，agent 间协作（Agent Card / 任务 / 传输），与 MCP（agent ↔ 工具）互补。
_Avoid_: agent 通信协议

**Multimodal**:
多模态，视觉输入（图像理解/OCR/截图）+ 多模态消息结构。
_Avoid_: 多模态输入

**CodeInterpreter**:
代码解释器，agent 生成并执行代码（E2B / Docker 沙箱），stdout / stderr / 文件产物回填。
_Avoid_: 代码执行器

### 架构与策略

**Planning**:
规划范式，含 ReAct / Plan-and-Execute / Reflection / Replan。
_Avoid_: 规划模式

**Workflow**:
工作流，确定性、可控、便宜的编排，与动态路径的 Agent 相对。
_Avoid_: 流程编排

**FrameworkComparison**:
框架对比，LangChain / LangGraph / DeepAgents / 手写的 tradeoff，以及何时用框架、何时自研。
_Avoid_: 框架选型

### 横切基础

**PromptEngineering**:
Prompt 工程，system prompt 设计 + few-shot / CoT + 动态 few-shot，管「指令怎么写」；与上下文工程（管「信息怎么组织进窗口」）相对。
_Avoid_: 提示词设计

**ToolDesign**:
工具设计方法论，工具粒度（一个工具一件事）、数量与准确率 tradeoff、参数越少越好。
_Avoid_: 工具设计

### 评估与迭代

**AgentEval**:
Agent 评估体系，用评估集 + LLM judge 衡量 agent 质量（任务成功率/工具选择正确率/回答质量/轨迹质量）。
_Avoid_: agent 测试

**LLMJudgeBias**:
LLM 裁判的系统性偏差（位置偏差/长度偏差/自我偏好），需多 judge 交叉、交换顺序、结构化 rubric 缓解。
_Avoid_: 评分偏差

**Debugging**:
Agent 调试，通过 trace 回放与决策归因定位 agent 行为问题。
_Avoid_: 排错

**DataFlywheel**:
数据飞轮，badcase 收集 → 归类 → 改进 → 回归的持续优化闭环。
_Avoid_: 反馈循环

### 测试

**MockLLM**:
mock 模型，固定返回 / 脚本化序列返回 / 录制回放，让 loop 测试不依赖真实 LLM。
_Avoid_: 模型桩

**DeterministicTest**:
确定性测试，temperature=0 + seed + 注入随机源保证同输入同输出，避免 flaky。
_Avoid_: 稳定测试

**TrajectoryAssertion**:
轨迹断言，断言工具调用的顺序与参数，而非仅最终答案。
_Avoid_: 调用断言

**SnapshotTest**:
快照/契约测试，对 checkpoint、事件流做快照对比防回归。
_Avoid_: 快照对比

### 工程化与部署

**Statelessness**:
无状态化，状态外置 Redis/Postgres，进程可水平扩；发布时对长任务做 drain（等完成/迁移/断点续跑）。
_Avoid_: 无状态服务

**AsyncioModel**:
asyncio 并发模型，事件循环 / 阻塞 IO 陷阱 / gather 异常 / 取消语义。
_Avoid_: 异步模型

**LatencyOptimization**:
延迟优化，TTFT vs 总延迟、流式感知延迟、并行 vs 串行工具权衡。
_Avoid_: 性能优化

**EngineeringFoundation**:
工程化底座，连接管理 / 配置管理 / schema 迁移 / 依赖管理 / 异常上报 / 资源泄漏的综合。
_Avoid_: 工程基础

### 客服 demo 领域

**Ticket**:
客户问题的一次记录，可被创建、追踪、升级。
_Avoid_: 工单记录, issue

**Escalation**:
将无法自动处理的 Ticket 升级给人工客服。
_Avoid_: 转人工, 升级
