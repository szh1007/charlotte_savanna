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
├── templates/
│   ├── minimall/            # 商城页面模板（base + partials）
│   ├── charplot/            # report_share.html（/r/{slug} 公开分享页）
│   └── admin/               # 自定义 Admin 模板
├── sh/                      # 各子项目启动脚本（后端/AI 端 + 前端, 见快速开始启动表）
├── demo/                    # 自学教程（非业务代码）
│   └── SUMMARY.md           # 知识点学习总结
├── docs/                    # Agent 定义、triage 规范、学习笔记
├── .scratch/                # 本地 Issue Tracker（Markdown, 按 feature-slug 分目录）
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

所有子项目后端/前端脚本均在 `sh/` 目录；前端脚本首次运行自动执行 `npm install`（deep_search / menu 对应 `ui/` 目录，其余为 `frontend/`）。Django 主项目依赖 MySQL + Redis；charplot 额外依赖 Milvus + modelscope 本地模型。

---

## 环境变量

统一通过 `.env` 管理（模板见 [`.env.example`](.env.example)），子项目另持有独立 `.env`（`project/*/.env`，不提交）。主要分组：

- **LLM（Chat）**：`DEEPSEEK_*`（含 `DEEPSEEK_MODEL_NAME`）
- **LLM（Embedding）**：`CLOSEAI_*`
- **数据库**：`MYSQL_*`（Django + demo）、`PGSQL_*`（PostgreSQL demo）
- **缓存 / 向量**：`REDIS_URL`、`MILVUS_*`（`MILVUS_URL` / `MILVUS_DATABASE_NAME` / `MILVUS_COLLECTION_NAME`）
- **子项目专用**：`MENU_*`（menu）、`DS_*`（deep_search）、`RK_*`（rag_knowledge）、`CHARPLOT_*`（charplot, 含 `CHARPLOT_MODELSCOPE_ROOT` 本地模型根与两端同值的 `CHARPLOT_INTERNAL_TOKEN`）；video_downloader 独立 `.env`（`MEMBER_KEY` / `BILI_COOKIE` / `LLM_*` / `ASR_*`）；rag_text2sql 不依赖 `.env`（配置在子项目 `conf/*.yaml`，本地私有不提交）
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
| [docs/](docs/) | Agent 定义、triage 规范、学习笔记 |

---

## 开发规范

代码质量通过 **Ruff**（`line-length = 88`）+ **pre-commit**（ruff / codespell / conventional-commits）保障；Git 提交遵循 Conventional Commits，敏感信息经 `.env` 环境变量隔离，不进入版本控制。完整约定见 [CLAUDE.md](CLAUDE.md)。
