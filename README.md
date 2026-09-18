# charlotte_savanna

<div align="center">

**全栈技术学习项目** — 以 Django / FastAPI 为骨架，贯通 Web 后端、LLM Agent、向量检索与 Vue 前端

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-6.0-092E20?style=flat&logo=django&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.139-009688?style=flat&logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-4FC08D?style=flat&logo=vuedotjs&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-1.3-1C3C3C?style=flat&logo=langchain&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C?style=flat&logo=langchain&logoColor=white)
![DeepAgents](https://img.shields.io/badge/DeepAgents-0.7-1C3C3C?style=flat&logo=langchain&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-4479A1?style=flat&logo=mysql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-FF4438?style=flat&logo=redis&logoColor=white)
![Milvus](https://img.shields.io/badge/Milvus-00A1EA?style=flat&logo=milvus&logoColor=white)

</div>

---

## 项目简介

个人技术学习项目，初始化于 2026-04。围绕 **Web 后端 → LLM Agent → 向量检索 → 前端** 一条完整链路，实践 Django、DRF、FastAPI、LangChain、LangGraph、DeepAgents、Vue 3 等技术栈。

代码按「业务模块 + 子项目 + 自学 demo」组织；正式「主流程」尚未确定，现有模块均为学习/测试性质：

| 模块 | 定位 | 说明 |
|------|------|------|
| `app/minimall/` | 业务模块（测试原型） | Django 商城：DRF API + 页面 + Redis 缓存 |
| `project/deep_search/` | 子项目 | 深度检索智能体（DeepAgents + FastAPI + Vue） |
| `project/menu/` | 子项目 | 餐厅智能助手（LangChain Agent + FastAPI + Vue） |
| `project/video_downloader/` | 子项目 | B 站视频下载站（FastAPI + yt-dlp + Vue，AI 视频总结） |
| `project/rag_knowledge/` | 子项目 | 工业级 RAG 知识库问答（LangGraph 双图 + Milvus + 评估体系） |
| `project/rag_text2sql/` | 子项目 | RAG Text2SQL 查询智能体（LangGraph + Qdrant/ES + MySQL 双库 + Vue） |
| `app/charplot/` + `project/charplot/` | 子项目 | AI 闯关学习网站（双后端: Django 账号/闯关规则 + FastAPI AI 能力 + Vue, 三件套实践） |
| `CharAgent/` | 子项目 | 从零手写的 agent runtime 框架（P0 已交付: 模型/工具/loop/事件/重试/checkpoint/数据层 + CLI 演示, 业务无关, 不依赖 Django） |
| `CharService/` | 子项目 | 企业级电商智能客服业务层（minimall internal API + 内部网关 + 身份服务三层对接, 12 工具 + 审批/接管台 + Vue） |
| `demo/` | 自学教程 | Python / LangChain / LangGraph / DeepAgents / FastAPI 教程 |

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **语言** | Python 3.13 | 类型注解、asyncio、ContextVar |
| **Web 框架** | Django 6.0 / FastAPI 0.139 | Django 用于 minimall / charplot 主应用，FastAPI 用于六个子项目 AI 端 |
| **Django 扩展** | DRF + django-filter + django-mptt | REST API、过滤、树形分类 |
| **ORM / DB** | PyMySQL / SQLAlchemy 2.0 | MySQL（参数化查询） |
| **缓存** | Redis（django-redis / redis-py） | L2 缓存、击穿/穿透/雪崩防护、熔断 |
| **LLM 框架** | LangChain 1.3 + LangGraph 1.2 | LCEL、`@tool`、`StateGraph` + checkpointer |
| **Agent** | DeepAgents 0.7 | `create_deep_agent` 多 subagent 协作 |
| **向量数据库** | Milvus / Qdrant / ChromaDB / FAISS | 语义检索（HNSW + 余弦相似度） |
| **全文检索** | Elasticsearch（ik 分词） | 字段取值索引（rag_text2sql） |
| **知识库** | RAGFlow | 企业知识库问答 |
| **RAG 组件** | BGE-M3 / bge-reranker-large / MinerU / MinIO / MongoDB | 混合向量、精排、PDF 解析、对象存储、会话历史（rag_knowledge）；bge-m3 + bge-reranker-v2-m3 本地模型（charplot, modelscope 预下载） |
| **搜索** | Tavily | 联网检索 |
| **视频处理** | yt-dlp + ffmpeg + SenseVoice | 下载引擎、音视频合并、ASR 转写（video_downloader） |
| **前端** | Vue 3 + Vite + TS/JS + Element Plus | 组合式 API、WebSocket / SSE |
| **代码质量** | Ruff + pre-commit | 静态检查、格式化、提交校验 |
| **配置管理** | python-dotenv / OmegaConf | `.env` 环境变量隔离；rag_text2sql 用 conf/*.yaml |

> 具体模型名、API 端点与 Key 配置参见 `.env.example`。

---

## 项目结构

```
charlotte_savanna/
├── charlotte_savanna/       # Django 配置包（settings 拆分 dev/prod）
├── app/
│   ├── minimall/            # 商城业务（Django，9 个模型 + 缓存）
│   └── charplot/            # CharPlot 闯关学习数据端（Django，12 表 + 规则层）
├── project/
│   ├── deep_search/         # 深度检索智能体（DeepAgents + FastAPI + Vue）
│   ├── menu/                # 餐厅智能助手（LangChain Agent + FastAPI + Vue）
│   ├── video_downloader/    # B 站视频下载站（FastAPI + yt-dlp + Vue）
│   ├── rag_knowledge/       # 工业级 RAG 知识库问答（LangGraph + Milvus + 评估体系）
│   ├── rag_text2sql/        # RAG Text2SQL 查询智能体（LangGraph + Qdrant/ES + MySQL 双库）
│   └── charplot/            # CharPlot AI 能力端（FastAPI + LangGraph/DeepAgents/LangChain + Vue）
├── CharAgent/               # 从零手写的 agent runtime 框架（model/tool/agent/stream/hooks/retry/checkpoint/db + client）
├── CharService/             # 企业级电商智能客服业务层（api/channels/identity/gateway/tools/frontend; 规划中, 待 issue 01 实施）
├── templates/
│   ├── minimall/            # 商城页面模板（base + partials）
│   ├── charplot/            # report_share.html（/r/{slug} 公开分享页）
│   └── admin/               # 自定义 Admin 模板
├── sh/                      # 各子项目启动脚本（后端/AI 端 + 前端, 见快速开始启动表）
├── demo/                    # 自学教程（非业务代码）
│   └── SUMMARY.md           # 知识点学习总结
├── docs/                    # Agent 定义、triage 规范、学习笔记
├── .scratch/                # 本地 Issue Tracker（Markdown, 按 feature-slug 分目录: CharAgent / CharService）
├── requirements.txt         # Python 依赖
├── .env.example             # 环境变量模板
└── CLAUDE.md                # 项目上下文与开发规范
```

---

## 核心模块

### minimall — 商城（Django 测试原型）

| 维度 | 内容 |
|------|------|
| 模型 | 9 个：Profile / Category(MPTT) / Product / ProductImage / Cart / CartItem / ShippingAddress / Order / OrderItem |
| API | DRF：认证 / 商品 / 购物车 / 地址 / 订单 / 充值 |
| 页面 | CBV：商品列表 / 详情 / 购物车 / 下单 / 个人中心 |
| 缓存 | Redis L2：击穿（SETNX + Pub-Sub）、穿透（空值）、雪崩（随机 TTL）、熔断 |

### deep_search — 深度检索智能体（子项目）

基于 **DeepAgents** 构建主智能体 + 三个专家子智能体，覆盖企业级信息检索与文档生成。

- **主智能体**：任务规划、信息汇总、文档生成（Markdown → PDF）
- **子智能体**：网络搜索（Tavily）/ 数据库查询（MySQL）/ RAGFlow 知识库
- **会话隔离**：`ContextVar` 协程级上下文 + `InMemorySaver` 按 `thread_id` 记忆
- **实时交互**：FastAPI WebSocket 流式上报工具调用与任务进度
- 详见 [`project/deep_search/README.md`](project/deep_search/README.md)

### menu — 餐厅智能助手（子项目）

基于 **LangChain Agent** 实现「一绪寿喜烧」餐厅订座与菜单查询。

- **Agent 工具**：特色主菜查询（MySQL）/ 口味语义检索（Milvus）/ 餐位预订（参数化防注入）
- **FAQ 推荐**：Redis 存储 + Embedding 余弦相似度，输入防抖实时推荐
- **流式对话**：FastAPI SSE（`text/event-stream`）逐 token 输出
- 详见 [`project/menu/README.md`](project/menu/README.md)

### video_downloader — B 站视频下载站（子项目）

基于 **FastAPI + yt-dlp + Vue 3** 的哔哩哔哩免费视频下载网站，实践「文档先行（CONTEXT/ADR/PRD）→ 分步实现 → 测试验收」工程模式。

- **下载流程**：粘贴链接 → 解析清晰度档位 → 批量下载（队列顺序执行 + 并发槽调度）→ 临时直链交付
- **无数据库**：任务 / 队列 / 会员会话全部内存态，交付文件 TTL 到期自动清理；仅支持 B 站免费公开视频（URL 白名单校验）
- **付费差异（后端强制）**：免费档限 720p / 1 并发 / 队列 5 / 直链 24h；会员密钥解锁全部清晰度 / 3 并发 / 队列 50 / 直链 72h
- **AI 视频总结**：官方字幕快路径 → SenseVoice 兜底转写 → DeepSeek 生成转录 / 总结 / 思维导图 / AI 问答
- **实时进度**：FastAPI SSE 推送任务状态（`task-update` + 心跳）
- 详见 [`project/video_downloader/README.md`](project/video_downloader/README.md)

### rag_knowledge — 工业级 RAG 知识库问答（子项目）

基于 **LangGraph** 双图的垂直领域知识库问答系统，面向产品说明书 / 技术文档，实践「检索增强生成 + 离线评估」完整闭环。

- **加载链路**：PDF（MinerU 解析）/ Markdown → 图片语义化（VL + MinIO）→ 标题级分块 → 主体识别 → BGE-M3 混合向量 → Milvus 索引
- **查询链路**：问题改写 + 主体确认 → 三路并行召回（向量 / HyDE / Tavily）→ RRF 融合 → bge-reranker 精排 → 溯源回答（SSE 流式）
- **会话历史**：MongoDB 存储，多轮指代消解
- **评估体系**：golden 题库（50 用例）+ 4 层检索指标（精确率 / 召回率 / 必命中率 / MRR@5 / NDCG@5），报告落盘 `app/rag_eval/artifacts/`
- 详见 [`project/rag_knowledge/README.md`](project/rag_knowledge/README.md)

### rag_text2sql — RAG Text2SQL 查询智能体（子项目）

基于 **LangGraph** 的数据仓库自然语言查询系统，面向数仓星型模型，实践「元数据三级语义索引 + Schema Linking + 生成-校验闭环」的 Text2SQL 路线。

- **元数据建模**：`conf/meta_config.yaml` 声明式建模表 / 字段 / 指标（含别名与口径），`python -m app.scripts.build_meta` 幂等构建
- **三级语义索引**：列 / 指标按 name / description / alias 向量化入 Qdrant（bge-large-zh-v1.5），维度列字段取值全量同步 ES（ik 分词）
- **查询链路**：jieba 关键词 → LLM 语义扩展 → 三路并行召回（列 / 指标 / 取值）→ 合并补齐（指标关联列 + 主外键）→ LLM 过滤表字段与指标 → 生成 SQL → 真实执行校验 → 失败自动校正 → 执行返回（SSE 流式）
- **双库架构**：MySQL `meta`（元数据）+ `dw`（数仓业务数据，端口 3307）；配置用本地私有 conf/*.yaml（不提交）
- **前端**：Vue 3 + Vite（SSE 步骤流 + 结果表格），召回率/准确率排查手册见子项目 README §6
- 详见 [`project/rag_text2sql/README.md`](project/rag_text2sql/README.md)

### charplot — AI 闯关学习网站（双后端子项目）

输入想学的知识（一句话 / 文档 / 网页 / 管理员预建知识库）→ AI 自动获取并解构成技能树图谱 → 渐进生成闯关题目 → 游戏化答题（心动值 / 连胜 / XP）→ 通关复盘可分享。实践「Django 状态端 + FastAPI AI 能力端」双后端微服务与 LangGraph / DeepAgents / LangChain 三件套分工（文档先行模式：CONTEXT / CONTRACT / DESIGN / QA + 4 个 ADR + 14 个 Issue tickets）。

- **知识管道（LangGraph）**：解析（txt/md/html/pdf/docx/pptx/网页链接）→ LLM 主内容分析 → 联网搜索增强（Tavily / Context7 / 文档 / 知识库检索源可插拔）→ 图谱解构（章节 → 知识点 + 前置依赖边）
- **闯关规则（Django 纯规则, 无 LLM）**：判分（选择/判断/填空三题型）/ 5 心动值安全失败 / 断点续答重开 / XP 等级 / 连胜冻结 / 间隔复习混入 Top 20% 易错题（易错分 × 时间衰减）
- **RAG 知识库（LangChain, FastAPI 侧）**：管理员上传文档 → bge-m3 切分 embedding → Milvus 混合检索 + query rewrite + bge-reranker-v2-m3 精排（本地 modelscope 模型, rerank 缺失自动降级）→ 主题卡片直达 Journey
- **后台分析**：掌握度矩阵 / 活动统计 / 易错清单（事实聚合）+ LLM 文字版状态总结 + 题目反馈标记
- 详见 [`project/charplot/README.md`](project/charplot/README.md)

### CharAgent — 从零手写的 agent runtime 框架（子项目）

把主流 Agent 框架（LangChain / LangGraph / DeepAgents）封装起来的「模型 ↔ 工具」循环、状态管理、流式推送全部摊开手写一遍：模型接入、工具注册、并行执行、错误自纠错、循环防护、断点续跑、重试退避，全部可读、可测、可演示。**不依赖 Django / FastAPI**，纯 Python 库 + 一个命令行入口。

- **模型层**：薄 `ChatModel` 协议（`generate(messages, tools) -> ModelResponse`）+ httpx 裸调与 openai SDK 双适配器（行为一致性由同一组契约测试约束，看清「SDK 帮我藏了什么」）
- **工具层**：`@tool` 装饰器自动生成 JSON schema（pydantic 与手写 typing 双引擎对照）；工具失败给**可操作错误**（「order_no 应为 14 位数字」而非 422），模型据此自纠错
- **主循环**：手写 while 循环 —— 模型决策 → 并行执行工具 → 结果回填 → 再决策；三种软限制刹车（轮数 / token / 时长）+ kill switch 即时打断；length 截断走续写或精简
- **流式事件**：六类事件（thinking / tool_call / tool_result / reasoning / final / error）+ 序号 + 四条顺序不变量；hook 注册表五个触发点（空注册零开销）
- **重试**：指数退避 + jitter，只重试瞬态错误（429 / 5xx / 超时），组合在协议层——loop 零改动
- **断点续跑**：每轮落一帧快照，内存 / Redis（流式历史）/ Postgres（全历史）三实现配置切换；恢复时已完成的工具**不会重跑**，从老快照恢复还能岔出新分支
- **数据层**：五实体（Thread / Run / Message / ToolCall / Checkpoint）+ 状态机规则 + 仓储，alembic 统一迁移
- **CLI 演示**：`python -m CharAgent.client` 带工具问答端到端，事件流实时打在终端；Ctrl-C 打断后**已完成的工作会收回对话历史**，直接说一句「继续」就接着跑（`/resume` 则走快照恢复：计数器接续），快照后端三选一（P0 验收线）
- 设计与难点文档见 [`CharAgent/docs/`](CharAgent/docs/)（DESIGN / 术语表 CONTEXT / design 01~05 / 9 个 ADR / 70 个编号难点）

### CharService — 企业级电商智能客服业务层（子项目）

在 CharAgent 这个业务无关的框架上，构建一个**对标企业生产形态**的电商智能客服：**agent 不直连业务库、不持有业务凭证**，而是经内部网关调业务系统的 internal API。目的是把「agent 怎么对接已有业务系统」讲成有取舍的架构决策，而不只是「能跑通」。

- **三层对接**（ADR-0008）：业务系统（minimall 新增 `/api/minimall/internal/support/*`，按任务粒度设计而非 1:1 映射 REST）+ 内部网关（agent 的**唯一入口**：验签 + 限流 + 双粒度审计 + 转发）+ 身份服务（RS256 签发委托 token）
- **委托身份**（ADR-0009）：身份在渠道层确定、会话绑定；**工具签名里没有 `user_id`**（运行时注入）——prompt injection 诱导也无法越权查别人的单；agent 无 token 签发权（私钥只在身份服务，网关只持公钥）
- **写操作三级**：L0 只读自由调用 / L1 可逆写（加购）直接执行 + 回显 / L2 终态或资金写走确认——**内部审批**（金额分层 + 角色分离：客服只能发起，主管/风控才能批）与**用户确认**（结构化 `confirm_token`，模型无法自己「确认」）是两条不同的挂起路径
- **异步退款**：提交即返回 `refund_id`，后台 worker（**在 minimall 侧**，因为要同事务改 MySQL）推进状态，agent 不挂起等待
- **双粒度审计**：工具调用（谁 / 代表谁 / 参数摘要）+ 数据访问（资源 ID + 敏感字段命中），合规上能回答「谁在什么时候看了谁的什么数据」
- 规格与 10 个垂直切片 issue 见 [`.scratch/CharService/`](.scratch/CharService/)，对接设计见 [`CharAgent/docs/adr/0008`](CharAgent/docs/adr/0008-business-integration-topology.md)、[`0009`](CharAgent/docs/adr/0009-delegated-identity-and-write-tiers.md)

### demo — 自学教程（非业务）

Python 基础、LangChain 1.3、LangGraph 1.2、DeepAgents 0.7、FastAPI 的渐进式教程，仅作学习参考。另有 [`SUMMARY.md`](demo/SUMMARY.md)（知识点学习总结）。

---

## 快速开始

### 环境要求

- Python 3.13、Node.js 18+
- MySQL、Redis（必需）；Milvus（menu 语义检索 / charplot 知识库 RAG 需要）

### 安装

```bash
# 1. 创建虚拟环境
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env               # 填入真实 API Key
```

### 启动

| 模块 | 后端 | 前端 |
|------|------|------|
| minimall | `python manage.py runserver` | 内建页面 |
| deep_search | `sh/deep_search_backend.sh` | `sh/deep_search_frontend.sh` |
| menu | `sh/menu_backend.sh` | `sh/menu_frontend.sh` |
| video_downloader | `sh/video_downloader_backend.sh` | `sh/video_downloader_frontend.sh` |
| rag_knowledge | `sh/rag_knowledge.sh`（8100, 无独立前端） | 内建页面 |
| rag_text2sql | `sh/rag_text2sql_backend.sh` | `sh/rag_text2sql_frontend.sh` |
| charplot | Django 主项目 `python manage.py runserver`（8000）+ `sh/charplot_backend.sh`（AI 端 8004） | `sh/charplot_frontend.sh`（9004） |
| CharAgent | 仓库根 `python -m CharAgent.client`（命令行演示, 非常驻服务, 无脚本） | 无（终端界面） |
| CharService【待建：issue 01】 | `sh/charservice_backend.sh`（主服务 10070 + 内部网关 10071 + 身份服务 10072） | `sh/charservice_frontend.sh`（10079） |

所有子项目后端/前端脚本均在 `sh/` 目录；前端脚本首次运行自动执行 `npm install`（deep_search / menu 对应 `ui/` 目录，其余为 `frontend/`）。Django 主项目依赖 MySQL + Redis；charplot 额外依赖 Milvus + modelscope 本地模型；CharService 依赖 MySQL + Postgres + Redis + Milvus，并要求 minimall 的 `internal/support` 端点可用。

---

## 环境变量

统一通过 `.env` 管理（模板见 [`.env.example`](.env.example)），子项目另持有独立 `.env`（`project/*/.env`，不提交）。主要分组：

- **LLM（Chat）**：`DEEPSEEK_*`（含 `DEEPSEEK_MODEL_NAME`）
- **LLM（Embedding）**：`CLOSEAI_*`
- **数据库**：`MYSQL_*`（Django + demo）、`PGSQL_*`（PostgreSQL demo；CharAgent 的快照与数据层缺专用配置时回退用它们）
- **缓存 / 向量**：`REDIS_URL`、`MILVUS_*`（`MILVUS_URL` / `MILVUS_DATABASE_NAME` / `MILVUS_COLLECTION_NAME`）
- **子项目专用**：`MENU_*`（menu）、`DS_*`（deep_search）、`RK_*`（rag_knowledge）、`CHARPLOT_*`（charplot, 含 `CHARPLOT_MODELSCOPE_ROOT` 本地模型根与两端同值的 `CHARPLOT_INTERNAL_TOKEN`）、`CHARAGENT_*`（CharAgent：快照后端 `CHARAGENT_CHECKPOINT_*` + 数据层连接 `CHARAGENT_DB_*`）、`CHARSERVICE_*`（CharService：内部网关/身份服务地址、委托 token 私钥路径与 TTL、退款审批金额阈值）；video_downloader 独立 `.env`（`MEMBER_KEY` / `BILI_COOKIE` / `LLM_*` / `ASR_*`）；rag_text2sql 不依赖 `.env`（配置在子项目 `conf/*.yaml`，本地私有不提交）
- **Django**：`DJANGO_*`
- **外部服务**：`TAVILY_API_KEY`、`LANGSMITH_*`

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [CLAUDE.md](CLAUDE.md) | 项目上下文、框架规范、开发约定 |
| [demo/SUMMARY.md](demo/SUMMARY.md) | 知识点学习总结（LangChain/LangGraph/DeepAgents） |
| [project/deep_search/README.md](project/deep_search/README.md) | deep_search 子项目文档 |
| [project/menu/README.md](project/menu/README.md) | menu 子项目文档 |
| [project/video_downloader/README.md](project/video_downloader/README.md) | video_downloader 子项目文档 |
| [project/rag_knowledge/README.md](project/rag_knowledge/README.md) | rag_knowledge 子项目文档 |
| [project/rag_text2sql/README.md](project/rag_text2sql/README.md) | rag_text2sql 子项目文档 |
| [project/charplot/README.md](project/charplot/README.md) | charplot 子项目文档（架构 / 流程 / 启动 / 已知问题） |
| [CharAgent/docs/](CharAgent/docs/) | CharAgent 框架文档（DESIGN / 术语表 / design 01~06 / ADR 0001~0010 / 70 个编号难点） |
| [.scratch/CharService/PRD.md](.scratch/CharService/PRD.md) | CharService 规格（三层对接 / 工具集 / 写操作分级 / 10 个垂直切片） |
| [docs/](docs/) | Agent 定义、triage 规范、学习笔记 |

---

## 开发规范

代码质量通过 **Ruff**（`line-length = 88`）+ **pre-commit**（ruff / codespell / conventional-commits）保障；Git 提交遵循 Conventional Commits，敏感信息经 `.env` 环境变量隔离，不进入版本控制。完整约定见 [CLAUDE.md](CLAUDE.md)。
