# handcraft_agent

从零手写的轻量 AI Agent 运行时框架 + 智能客服 demo。目标：吃透 AI Agent 底层原理（agent loop、状态管理、流式、安全、成本、可观测、测试、评估），覆盖企业级 Agent 项目的常见问题、难点与面试考点。

## 项目背景

主流 Agent 框架（LangChain / LangGraph / DeepAgents / Claude Agent SDK）把 agent loop 封装成黑盒——开发者只配 tools + prompt，框架内部「模型 ↔ 工具」的循环、状态管理、流式推送都被藏起来了。

本项目反其道而行，**手写 agent loop**，把每一层暴露出来：

- 模型如何被调用、工具如何被执行、结果如何回填
- 状态如何持久化、断点如何续跑
- 流式事件如何产生、如何推给前端
- 重试、熔断、超时、幂等如何兜底
- prompt injection、沙箱、HITL、脱敏、审计如何落地

**与 LangGraph 的关系**：LangGraph 用 StateGraph 显式建模「状态机 + 节点」，本项目用最朴素的 while 循环实现同一件事。理解了手写版，再看 LangGraph / DeepAgents，就知道它们替你做了什么、为什么那么设计。

## 核心架构

agent loop 是项目核心，本质是一个 while 循环：

```text
while not done:
    response = model.generate(messages, tools)       # 模型决策：给答案 or 调工具
    if response.has_tool_calls:                      # 模型要求调用工具
        results = execute_tools(response.tool_calls) # 并发执行所有工具
        messages.append(results)                     # 结果回填，进入下一轮
    else:                                            # 模型给出最终答案
        done = True
```

其中「模型决策 → 工具执行 → 结果回填」的一次完整迭代称为一个 **Turn**。回填不是把工具结果拼进文本，而是以一条 `tool_result` 消息的形式追加进 `messages`——模型下一轮通过完整消息历史理解执行情况。

在这个循环之上，逐层叠加生产级能力：

| 层 | 能力 | 对应难点 / 选型 |
|----|------|----------------|
| 模型层 | ChatModel 协议 + httpx/openai 双实现 | 选型 0001 / 0003 |
| 工具层 | @tool 装饰器 + JSON schema 生成 + 沙箱 + 工具设计 | #10, #24, #70 |
| 循环层 | 并行工具 + 错误自纠错 + 循环防护 | #1-3 |
| 状态层 | checkpoint 快照 + thread 分区 + 数据模型 | #5, #12, 选型 0002 |
| 流式层 | 四类事件 + SSE | #4, 选型 0005 |
| 可靠层 | 重试 / 熔断 / 超时 / 幂等 / 降级 | #13-19 |
| 队列层 | 并发排队 + 长任务异步化 | #20, 选型 0006 |
| 安全层 | 注入防护 / 沙箱 / HITL / 脱敏 / 审计 / 输出护栏 | #23-30 |
| 记忆层 | 四层记忆 + 多租户隔离 + 记忆机制 | #31-33 |
| 成本层 | 成本追踪 / token 计量 / 分级路由 / 缓存 / batch | #34-37 |
| 可观测层 | 日志 / 指标 / 告警 / 版本化 / trace | #38-41 |
| 评估层 | Agent 评估 / 调试 / 数据飞轮 | #58-60 |
| 测试层 | mock / 确定性 / 分层 / 轨迹断言 / 快照 | #61-63 |
| 工程层 | 部署扩展 / 异步并发 / 延迟优化 / 工程化底座 | #64-67 |

> #68/#69/#70 横切点贯穿所有层：工具描述优化与工具设计（tool 层）、温度/采样控制与 prompt 工程（model 层）、限流/背压（server 层）。

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.13 |
| 模型接入 | httpx 裸调 + openai SDK（DeepSeek `deepseek-v4-flash`，OpenAI 兼容 base_url） |
| Embedding | CloseAI（OpenAI 兼容代理）`text-embedding-3-large` |
| 存储 | Redis（Docker，缓存 / checkpoint / 分布式锁）+ Postgres（checkpoint 历史） |
| 向量库 | Milvus（本机 Docker，直接采用） |
| 文档解析 | pypdf / unstructured |
| 流式 | SSE（Server-Sent Events） |
| 队列 | 进程内 asyncio 队列（P1），预留分布式 MQ |
| 测试 | pytest + pytest-asyncio + respx（mock HTTP） |
| 日志 | structlog（P1 本地 JSON 文件）+ Loki（P2 对接） |
| 可观测 | Prometheus 文本指标（P1）+ Langfuse trace（P2 自托管） |
| 错误上报 | Sentry（P2 可选项，不做自托管） |
| 前端 demo | Vue 3 + EventSource |

## 技术选型

> 完整 ADR 见 `docs/adr/`（0001-0007）。选型 0001-0006 已自本文件拆出独立成文，内容以 ADR 文档为准；0007 为 P2 可插拔化架构决策。

| ADR | 决策 | 一句话理由 |
|-----|------|-----------|
| [0001](docs/adr/0001-thin-chatmodel-protocol.md) | 薄 `ChatModel` 协议，不做多 provider 抽象 | 胶水层偏离「手写 loop」主线 |
| [0002](docs/adr/0002-checkpoint-redis-postgres-dual.md) | Checkpoint 用 Redis + Postgres 双实现 | 两种存储语义差异，SQLite 无法体现 |
| [0003](docs/adr/0003-httpx-raw-plus-openai-sdk-dual-adapter.md) | httpx 裸调 + openai SDK 双适配器 | 看清协议细节 + 贴近生产实际 |
| [0004](docs/adr/0004-generic-framework-plus-support-demo.md) | 通用框架 + 电商售后客服 demo 双形态 | 框架难点全覆盖 + 业务落地自证 |
| [0005](docs/adr/0005-sse-streaming.md) | 流式输出用 SSE，非 WebSocket | 单向推送刚好匹配，可直通转发 |
| [0006](docs/adr/0006-inprocess-asyncio-task-queue.md) | 进程内 asyncio 队列起步，预留分布式 MQ | 单实例阶段零部署，抽象保切换 |
| [0007](docs/adr/0007-p2-pluggable-extension-points.md) | P2 模块可插拔化（事件总线 + hook + SPI） | P2 与 P0/P1 低耦合，互不影响验收 |

## 分阶段计划

| 阶段 | 范围 | 难点 | 产出物 | 验收标准 |
|------|------|------|--------|----------|
| **P0** | 核心 loop + 可靠性 + 测试 | #1-5、#10-11、#13、#61-63、#65 | model.py + tool.py + loop.py + stream.py + retry.py + checkpoint/ + tests/ | 带工具的 agent loop 跑通；checkpoint 可断点续跑；mock LLM 单测通过 |
| **P1** | 可靠性加固 + 安全 + RAG + demo | #6-7、#14-27、#29-30、#35、#38、#45-47 | server（SSE 接口）+ 客服 demo + 安全/降级/队列/RAG 模块 | 客服 demo 端到端跑通（提问→检索→回复→转人工）；SSE 流式输出；高危操作需审批 |
| **P2** | 记忆/成本/可观测/多 agent/能力扩展/评估/工程化 | #8-9、#12、#28、#31-34、#36-37、#39-44、#48-60、#64、#66-67 | 记忆/成本/可观测/多 agent/MCP/Skills/Eval/数据模型/部署/工程底座模块 | 完整生产级能力：多租户隔离、成本追踪、指标告警、多 agent 协作、评估回归、无状态水平扩展 |

> #68/#69/#70 横切点横跨 P0-P1：工具描述优化、温度/采样控制、prompt 工程、工具设计方法论属 P0；限流/背压属 P1。

## 难点清单

共 **70 个编号难点**，按 14 类组织。详细清单已拆分至 `docs/difficulties/`，索引如下：

| 类别 | 难点编号 | 详细文件 |
|------|---------|---------|
| ① 核心循环 | #1-12 | [01-core-loop.md](docs/difficulties/01-core-loop.md) |
| ② 稳定性/降级 | #13-22 | [02-stability.md](docs/difficulties/02-stability.md) |
| ③ 安全 | #23-30 | [03-security.md](docs/difficulties/03-security.md) |
| ④ 记忆 | #31-33 | [04-memory.md](docs/difficulties/04-memory.md) |
| ⑤ 成本 | #34-37 | [05-cost.md](docs/difficulties/05-cost.md) |
| ⑥ 可观测 | #38-41 | [06-observability.md](docs/difficulties/06-observability.md) |
| ⑦ 多 agent | #42-44 | [07-multiagent.md](docs/difficulties/07-multiagent.md) |
| ⑧ RAG | #45-49 | [08-rag.md](docs/difficulties/08-rag.md) |
| ⑨ 能力扩展 | #50-54 | [09-extensions.md](docs/difficulties/09-extensions.md) |
| ⑩ 架构与策略 | #55-57 | [10-architecture.md](docs/difficulties/10-architecture.md) |
| ⑪ 评估与迭代 | #58-60 | [11-evaluation.md](docs/difficulties/11-evaluation.md) |
| ⑫ 测试 | #61-63 | [12-testing.md](docs/difficulties/12-testing.md) |
| ⑬ 工程化与部署 | #64-67 | [13-engineering.md](docs/difficulties/13-engineering.md) |
| ⑭ 横切基础点 | #68-70 | [14-cross-cutting.md](docs/difficulties/14-cross-cutting.md) |

### 难点编号分布

| 类别 | 编号 | 数量 |
|------|------|------|
| ① 核心循环 | 1-12 | 12 |
| ② 稳定性/降级 | 13-22 | 10 |
| ③ 安全 | 23-30 | 8 |
| ④ 记忆 | 31-33 | 3 |
| ⑤ 成本 | 34-37 | 4 |
| ⑥ 可观测 | 38-41 | 4 |
| ⑦ 多 agent | 42-44 | 3 |
| ⑧ RAG | 45-49 | 5 |
| ⑨ 能力扩展 | 50-54 | 5 |
| ⑩ 架构与策略 | 55-57 | 3 |
| ⑪ 评估与迭代 | 58-60 | 3 |
| ⑫ 测试 | 61-63 | 3 |
| ⑬ 工程化与部署 | 64-67 | 4 |
| ⑭ 横切基础点 | 68-70 | 3 |

## 面试要点导航

把难点按面试主题归类，复习时快速定位：

| 面试主题 | 对应难点 |
|----------|----------|
| 手写 agent loop 完整实现 | #1-5, #10-11 |
| 测试（mock / 确定性 / 分层 / 轨迹断言 / 快照） | #61-63 |
| 生产可靠性（重试 / 熔断 / 超时 / 幂等 / 降级 / 限流 / 分布式锁） | #13-22 |
| 并发与异步（请求排队 / 长任务异步化 / 分布式锁 / asyncio） | #20-21, #65 |
| 安全（注入 / 沙箱 / HITL / 脱敏 / 审计 / 合规删除 / 输出护栏 / SSRF） | #23-30 |
| 记忆系统（四层记忆 / 多租户隔离 / 记忆机制） | #31-33 |
| 成本优化（追踪 / token 计量 / 分级路由 / 缓存 / batch） | #34-37 |
| 可观测（日志 / 指标 / 告警 / 版本化 / trace） | #38-41 |
| 多 agent 协作（拓扑 / 协作 / 控制流 / A2A） | #42-44, #52 |
| RAG（知识注入选型 / 数据摄取 / 检索 / 评估 / 前沿） | #45-49 |
| 协议扩展（MCP / Skills） | #50-51 |
| 架构设计（Planning / Workflow vs Agent / 框架对比） | #55-57 |
| 上下文与 prompt 工程（窗口管理 / lost-in-the-middle / prompt 工程） | #6-9, #69 |
| 数据模型与部署（表结构 / 无状态化 / 延迟优化 / 工程底座） | #12, #64, #66-67 |
| 多模态与执行环境（多模态 / 代码解释器） | #53-54 |
| 评估与迭代（评估体系 / 调试 / 数据飞轮） | #58-60 |

## 目录结构

```text
handcraft_agent/
├── README.md                   # 项目说明 + 70 点难点清单 + 面试导航
├── CONTEXT.md                  # 领域术语表（glossary）
├── docs/
│   ├── design/                 # 详细设计文档（01-架构 / 02-数据模型 / 03-API / 04-测试 / 05-路线图）
│   └── adr/                     # 架构决策记录（0001-0007）
├── handcraft_agent/            # 框架包（agent runtime，业务无关）
│   ├── __init__.py
│   ├── model.py                # P0  ChatModel 协议 + httpx/openai 双实现 + reasoning 兼容
│   ├── tool.py                 # P0  @tool 装饰器 + JSON schema 生成
│   ├── loop.py                 # P0  手写 agent loop（并行/纠错/循环防护）
│   ├── stream.py               # P0  流式事件（SSE）
│   ├── retry.py                # P0  重试 + 退避 + 幂等键
│   ├── checkpoint/             # P0  base / memory / redis / postgres + 序列化协议
│   ├── guard.py                # P1  输入/输出护栏 + 脱敏 + HITL + 审计
│   ├── ratelimit.py            # P1  限流算法（固定/滑动窗口 + 令牌桶/漏桶）
│   ├── lock.py                 # P1  分布式锁
│   ├── rag/                    # P1  数据摄取 / 检索 / 评估
│   ├── plugins/                # P2  可插拔模块（配置注册 + 惰性 import，ADR-0007）
│   │   ├── memory/             # P2  四层记忆 + 多租户隔离
│   │   ├── cost/               # P2  成本追踪 + token 计量 + 分级路由 + 缓存 + batch
│   │   ├── observability/      # P2  日志 + 指标 + trace + 版本化
│   │   ├── multiagent/         # P2  Supervisor / P2P / handoff
│   │   ├── mcp/                # P2  MCP client + server
│   │   ├── skills/             # P2  Agent Skills
│   │   ├── eval/               # P2  评估 + 调试 + 数据飞轮
│   │   └── context_engineering/ # P2  上下文工程 + 意图澄清
├── server/                     # P1  FastAPI 服务（SSE 接口 + 任务队列）
├── demo/                       # P1  智能客服 demo（Ticket / Escalation）
└── tests/                      # P0  单元 / 集成 / E2E 测试
```

## 快速开始

> 各阶段运行入口随实现补充，规划如下：

| 阶段 | 运行入口 | 说明 |
|------|---------|------|
| P0 | `python -m handcraft_agent.cli` | CLI 跑通 loop（不依赖 server），checkpoint 断点续跑 + 单测 |
| P1 | `python -m server.main` + 前端 `npm run dev` | FastAPI + SSE，客服 demo 端到端 |
| P2 | 各插件按需启用 | 评估集跑分、trace 面板、记忆/多 agent 演示 |

## 文档索引

- [CONTEXT.md](CONTEXT.md) — 领域术语表
- [docs/design/](docs/design/) — 详细设计文档（01-架构总览 / 02-数据模型 / 03-Server API 与事件协议 / 04-测试计划 / 05-路线图）
- [docs/adr/](docs/adr/) — 架构决策记录（0001-0007，与实现同步演进）
- [docs/difficulties/](docs/difficulties/) — 70 个编号难点详细清单（14 个分类文件）
