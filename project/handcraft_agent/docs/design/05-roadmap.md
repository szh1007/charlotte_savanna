# 05 路线图（P0 → P1 → P2）

> 难点编号对应 README「难点清单」；阶段划分与 README「分阶段计划」一致。P2 全部模块走插件化挂载（ADR-0007），不回溯改造 P0/P1。

## P0：核心 loop + 可靠性 + 测试

目标：带工具的 agent loop 跑通；checkpoint 可断点续跑；mock 单测通过。**交付物：框架包可用（CLI 可演示）。**

| # | 任务 | 落点文件 | 难点 |
|---|------|---------|------|
| P0-1 | `ChatModel` 协议 + httpx 裸调 / openai SDK 双适配器（DeepSeek `deepseek-v4-flash`，含 reasoning_content 兼容分支） | `model.py` | #11, #68, ADR-0001/0003 |
| P0-2 | `@tool` 装饰器 + JSON schema 生成（参数设计、描述规范） | `tool.py` | #10, #70, #68 |
| P0-3 | 手写 agent loop：并行工具（gather + return_exceptions）、错误自纠错（可操作错误回填）、循环防护（max_turns / token 预算 / wall-clock / kill switch）、finish_reason 处理（length 截断） | `loop.py` | #1, #2, #3, #10, #65 |
| P0-4 | 流式事件状态机（四类事件 + reasoning 增量），事件总线 + hook 注册表骨架（空实现） | `stream.py` + `hooks.py` | #4, #11, ADR-0007 |
| P0-5 | 重试 + 指数退避 + jitter + 幂等键（只对瞬态错误重试，4xx 放弃） | `retry.py` | #13 |
| P0-6 | Checkpoint：序列化协议（JSON + 自定义序列化器 + schema 版本号）、InMemory / Redis / Postgres 三实现、time-travel 分支 | `checkpoint/` | #5, ADR-0002 |
| P0-7 | 五实体数据模型（thread/run/message/tool_call/checkpoint）定义 + Postgres 表 + alembic 初始化 | `models.py` + `alembic/` | #5, #12(实体部分) |
| P0-8 | 分层测试 + MockLLM（固定/脚本化/录制回放）+ 轨迹断言 + 快照测试 | `tests/` | #61, #62, #63 |

验收：CLI `python -m handcraft_agent.cli` 跑通带工具问答；checkpoint 中断续跑演示；`pytest tests/` 全绿。

## P1：可靠性加固 + 安全 + RAG + 客服 demo

目标：客服 demo 端到端（提问→检索→回复→转人工）；SSE 流式；高危操作审批。**交付物：server + 前端 + demo 全链路闭环。**

| # | 任务 | 落点文件 | 难点 |
|---|------|---------|------|
| P1-1 | FastAPI server：REST + SSE + TaskQueue（进程内 asyncio FIFO + 并发状态锁）+ 取消 + 长任务异步化 | `server/` | #20, ADR-0006 |
| P1-2 | 运行状态机 + 流式中断/取消（asyncio cancel 语义 + 资源释放 + 幂等键留痕） | `loop.py` + `server/` | #16, #18, #65 |
| P1-3 | 熔断 + failover、分层超时（model 60s / tool 10-30s / run 总超时）、工具取消 | `guard.py`（或独立 `circuit.py`） | #14, #15 |
| P1-4 | 幂等 + Saga 补偿（退款/通知场景）、分布式锁（Redis SETNX + TTL 续租 + owner 校验） | `retry.py` + `lock.py` | #17, #21 |
| P1-5 | 限流（固定/滑动窗口 + 令牌桶/漏桶，per user / tenant / model） | `ratelimit.py` | #22, #68 |
| P1-6 | 安全：输入/输出护栏（四层纵深）、Prompt Injection 防护、工具沙箱/最小权限（订单查询只读账号 + SQL 白名单）、SSRF 防护、输出护栏 + 引用溯源 | `guard.py` + demo 工具 | #23, #24, #29, #30 |
| P1-7 | HITL 高危操作审批（挂起持久化 → 审批恢复 / 拒绝回填 / 超时降级）、敏感信息脱敏（PII 结构化脱敏）、审计轨迹 | `guard.py` + `server/` | #25, #26, #27 |
| P1-8 | 业务降级兜底（模板 / FAQ 匹配 / workflow 路径 / 部分结果 / 转人工） | `server/` + demo | #19 |
| P1-9 | RAG：文档摄取（pypdf + 清洗 + 元数据 + chunk）、Milvus 检索（metadata 过滤 + 引用溯源）、语义缓存 SPI 预留 | `rag/` | #45, #46, #47 |
| P1-10 | 结构化输出（response_format + json_schema + 校验重试）、上下文压缩（摘要 + 截断，不破坏 tool 结构） | `loop.py` | #6, #7 |
| P1-11 | Token 计量（请求前预估 + usage 回填 + 预算挂钩） | `retry.py` / `model.py` | #35 |
| P1-12 | 结构化日志（structlog JSON + request_id/trace_id/thread_id 贯穿 + 脱敏前置）+ 指标最小集（Prometheus 文本格式） | `handcraft_agent/logging.py` + 核心层配置（observability 插件属 P2） | #38, #39 |
| P1-13 | 客服 demo：业务工具集（订单查询 / 物流 / 退款审批 / FAQ / 知识库检索 / 转人工）、Ticket/Escalation/Approval 表、转人工接管流程 | `demo/` | #19, #25 |
| P1-14 | 前端：Vue 3 + EventSource（`/chat` 用户端 + `/admin` 审批/接管台）、事件渐进渲染 | `ui/` | #4 |

验收：客服 demo 端到端跑通；SSE 流式输出；退款审批全流程（挂起→审批→恢复）；转人工接管；降级路径验证。

## P2：记忆 / 成本 / 可观测 / 多 agent / 能力扩展 / 评估 / 工程化（插件化）

目标：完整生产级能力。**全部经配置注册挂载（ADR-0007），不回溯改造 P0/P1。**

| # | 任务 | 落点 | 难点 |
|---|------|------|------|
| P2-1 | 上下文工程：窗口管理、动态组装、工具结果截断、lost-in-the-middle、prompt 缓存；意图识别 + 澄清 | plugins/context_engineering | #8, #9 |
| P2-2 | 数据模型补全：event 表（事件溯源）、TTL 清理 | models + alembic 迁移 | #12 |
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

- P0-1/P0-2 并行起步 → P0-3（loop）依赖前两者 → P0-4/P0-5/P0-6 并行 → P0-7 依赖 P0-6 → P0-8 贯穿全程
- P1-1 依赖 P0 全量；P1-6/P1-7 依赖 P1-2（状态机 + 取消）；P1-13 依赖 P1-1/P1-6/P1-7；P1-14 依赖 P1-1
- P2 全部模块独立于 P0/P1 验收，按需逐个挂载
