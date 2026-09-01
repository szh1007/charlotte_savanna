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
| `demo/` | 自学教程 | Python / LangChain / LangGraph / DeepAgents / FastAPI 教程 |

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **语言** | Python 3.13 | 类型注解、asyncio、ContextVar |
| **Web 框架** | Django 6.0 / FastAPI 0.139 | Django 用于 minimall，FastAPI 用于三个子项目 |
| **Django 扩展** | DRF + django-filter + django-mptt | REST API、过滤、树形分类 |
| **ORM / DB** | PyMySQL / SQLAlchemy 2.0 | MySQL（参数化查询） |
| **缓存** | Redis（django-redis / redis-py） | L2 缓存、击穿/穿透/雪崩防护、熔断 |
| **LLM 框架** | LangChain 1.3 + LangGraph 1.2 | LCEL、`@tool`、`StateGraph` + checkpointer |
| **Agent** | DeepAgents 0.7 | `create_deep_agent` 多 subagent 协作 |
| **向量数据库** | Milvus / ChromaDB / FAISS | 语义检索（HNSW + 余弦相似度） |
| **知识库** | RAGFlow | 企业知识库问答 |
| **RAG 组件** | BGE-M3 / bge-reranker-large / MinerU / MinIO / MongoDB | 混合向量、精排、PDF 解析、对象存储、会话历史（rag_knowledge） |
| **搜索** | Tavily | 联网检索 |
| **视频处理** | yt-dlp + ffmpeg + SenseVoice | 下载引擎、音视频合并、ASR 转写（video_downloader） |
| **前端** | Vue 3 + Vite + TS/JS + Element Plus | 组合式 API、WebSocket / SSE |
| **代码质量** | Ruff + pre-commit | 静态检查、格式化、提交校验 |
| **配置管理** | python-dotenv | `.env` 环境变量隔离 |

> 具体模型名、API 端点与 Key 配置参见 `.env.example`。

---

## 项目结构

```
charlotte_savanna/
├── charlotte_savanna/       # Django 配置包（settings 拆分 dev/prod）
├── app/minimall/            # 商城业务（Django，9 个模型 + 缓存）
├── project/
│   ├── deep_search/         # 深度检索智能体（DeepAgents + FastAPI + Vue）
│   ├── menu/                # 餐厅智能助手（LangChain Agent + FastAPI + Vue）
│   ├── video_downloader/    # B 站视频下载站（FastAPI + yt-dlp + Vue）
│   └── rag_knowledge/       # 工业级 RAG 知识库问答（LangGraph + Milvus + 评估体系）
├── templates/               # 全局模板（minimall + admin）
├── sh/                      # 启动脚本（deep_search / menu / video_downloader 前后端）
├── demo/                    # 自学教程（非业务代码）
│   └── SUMMARY.md           # 知识点学习总结
├── docs/                    # Agent 定义、triage 规范、学习笔记
├── .scratch/                # 本地 Issue Tracker（Markdown，当前为空）
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

### demo — 自学教程（非业务）

Python 基础、LangChain 1.3、LangGraph 1.2、DeepAgents 0.7、FastAPI 的渐进式教程，仅作学习参考。另有 [`SUMMARY.md`](demo/SUMMARY.md)（知识点学习总结）。

---

## 快速开始

### 环境要求

- Python 3.13、Node.js 18+
- MySQL、Redis（必需）；Milvus（menu 子项目语义检索需要）

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
| rag_knowledge | `python -m project.rag_knowledge.app.api.server` | 内建页面 |

子项目前端首次运行需在对应 `ui/` 目录执行 `npm install`。

---

## 环境变量

统一通过 `.env` 管理（模板见 [`.env.example`](.env.example)），子项目另持有独立 `.env`（`project/*/.env`，不提交）。主要分组：

- **LLM（Chat）**：`DEEPSEEK_*`（含 `DEEPSEEK_MODEL_NAME`）
- **LLM（Embedding）**：`CLOSEAI_*`
- **数据库**：`MYSQL_*`（Django + demo）、`PGSQL_*`（PostgreSQL demo）
- **缓存 / 向量**：`REDIS_URL`、`MILVUS_*`（`MILVUS_URL` / `MILVUS_DATABASE_NAME` / `MILVUS_COLLECTION_NAME`）
- **子项目专用**：`MENU_*`（menu）、`DS_*`（deep_search）、`RK_*`（rag_knowledge）；video_downloader 独立 `.env`（`MEMBER_KEY` / `BILI_COOKIE` / `LLM_*` / `ASR_*`）
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
| [docs/](docs/) | Agent 定义、triage 规范、学习笔记 |

---

## 开发规范

代码质量通过 **Ruff**（`line-length = 88`）+ **pre-commit**（ruff / codespell / conventional-commits）保障；Git 提交遵循 Conventional Commits，敏感信息经 `.env` 环境变量隔离，不进入版本控制。完整约定见 [CLAUDE.md](CLAUDE.md)。
