# CLAUDE.md — charlotte_savanna

> 通用规范（沟通风格、Git 操作、Python 编码、安全原则）参见系统级 `~/.claude/CLAUDE.md`

> 本文档仅包含 charlotte_savanna 项目特定内容。

---

## 1. 项目概述

**charlotte_savanna** 是以 **Django 6.0** 为骨架的个人技术学习项目，自学 Django、LangChain、LangGraph、FastAPI、DeepAgents、Vue 3 等技术栈。

项目初始化于 2026-04-27，当前处于活跃开发中。代码按「业务模块 + 子项目 + 自学 demo」组织；正式「主流程」尚未确定，现有模块均为学习/测试性质：

| 模块 | 类型 | 说明 |
|------|------|------|
| `app/minimall/` | 业务模块（测试原型） | 商城业务（DRF API + 页面，Redis 缓存） |
| `project/deep_search/` | 子项目 | DeepAgents 深度检索智能体（FastAPI + Vue 前端） |
| `project/menu/` | 子项目 | 餐厅智能助手（LangChain Agent + FastAPI + Vue） |
| `demo/` | 自学教程 | 非业务代码，见 §1.1 |

---

## 1.1 Demo 目录（非业务代码，分析/开发时请忽略）

根目录 `demo/` 下是个人自学教程代码，**不属于业务代码**。分析代码、重构、写测试、排查问题时均应跳过整个 `demo/` 目录：

| 目录 | 内容 |
|------|------|
| `demo/Base/` | Python 基础：OOP、装饰器、深拷贝、迭代器/生成器、多进程/多线程/协程 |
| `demo/LangChain_v1.3/` | LangChain 1.3 教程（model/prompt/tool/pydantic/agent/middleware/hook/memory/RAG） |
| `demo/LangGraph_v1.2/` | LangGraph 1.2 教程（basis/stream/interrupt/memory/tool/subgraph） |
| `demo/DeepAgent_v0.7/` | DeepAgent 0.7 教程（agent/subagent/interrupt/backend） |
| `demo/FastAPI/` | FastAPI 基础（含 ORM demo） |

所有自学教程统一放入 `demo/` 目录，仅作学习参考保留，后续不会被删除。**业务/子项目相关的工作（model 设计、view 编写、测试、性能分析、安全审计等）一律不涉及 `demo/` 目录。**

---

## 2. 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| **语言** | Python | 3.13 |
| **Web 框架** | Django（minimall）+ FastAPI（deep_search / menu） | 6.0 / 0.139 |
| **数据库** | MySQL + Redis 缓存（django-redis / redis-py） | — |
| **ORM** | SQLAlchemy（menu 子项目） | 2.0 |
| **Django 扩展** | DRF + django-filter + django-mptt | 3.17 / 25.2 / 0.18 |
| **LLM 框架** | LangChain + LangGraph | 1.3 / 1.2 |
| **Agent** | DeepAgents | 0.7 |
| **向量数据库** | ChromaDB + FAISS + RAGFlow + Milvus | — |
| **前端** | Vue 3 + Vite + TypeScript/JavaScript + Element Plus | — |
| **代码质量** | Ruff + pre-commit | — |
| **包管理** | pip + venv + npm | — |
| **环境管理** | python-dotenv (.env) | — |

> LLM 提供商、具体模型名、API 端点和 Key 配置参见 `.env.example`。具体模型列表不在本文档中维护，避免过时。

---

## 3. 项目结构

```
charlotte_savanna/
├── manage.py                    # Django CLI 入口（默认加载 settings.dev）
├── charlotte_savanna/           # Django 项目配置包
│   ├── settings/                #   设置拆分目录（原 settings.py）
│   │   ├── base.py              #   通用配置（DB/Redis/DRF/i18n/静态媒体）
│   │   ├── dev.py               #   开发环境（DEBUG=True, ALLOWED_HOSTS 宽松）
│   │   └── prod.py              #   生产环境（DEBUG=False, 安全加固）
│   ├── urls.py                  # 根路由（/、admin/、api/、shop/）
│   ├── wsgi.py / asgi.py        # 部署入口
│   └── __init__.py
├── app/                         # Django 业务子应用统一目录
│   └── minimall/                #   商城应用（AppConfig: MinimallConfig, label=minimall）
│       ├── models.py            #   9 个模型：Profile/Category(MPTT)/Product/ProductImage/Cart/CartItem/ShippingAddress/Order/OrderItem
│       ├── views_buyer.py       #   API 视图（DRF）
│       ├── views_html.py        #   页面视图（CBV）
│       ├── services.py          #   业务逻辑层（订单、余额等）
│       ├── serializers.py       #   DRF 序列化器
│       ├── cache.py             #   Redis 二级缓存（击穿/穿透/雪崩防护 + 熔断）
│       ├── signals.py           #   缓存失效 Signal（post_save/delete + on_commit）
│       ├── permissions.py       #   权限类
│       ├── context_processors.py / filters.py / utils.py
│       ├── urls_api.py          #   API 路由（/api/...）
│       ├── urls_html.py         #   页面路由（/shop/...）
│       ├── migrations/          #   17 个迁移
│       ├── tests/               #   test_api / test_models / test_services
│       ├── todo/redis.md        #   Redis 缓存设计文档（架构/风险/使用）
│       └── uploads/             #   本地文件上传（.gitignore 排除）
├── project/                     # 独立子项目（deep_search / menu）
│   ├── deep_search/             #   深度检索智能体（FastAPI + DeepAgents）
│   │   ├── agent/               #   main_agent + subagents（数据库查询/网络搜索/知识库）
│   │   ├── api/                 #   FastAPI server / context（会话）/ monitor
│   │   ├── tools/               #   mysql / pdf / markdown / ragflow / tavily / 文件读取 工具
│   │   ├── utils/               #   session / stream / path_utils / word_converter
│   │   ├── ragflow/             #   RAGFlow 集成 demo 与部署笔记
│   │   ├── ui/                  #   Vue 3 + Vite + TS 前端
│   │   ├── output/              #   生成结果输出
│   │   ├── prompt/              #   prompt 配置（prompt.yaml）
│   │   ├── updated/             #   （空占位）
│   │   ├── README.md            #   子项目文档
│   │   └── .env                 #   独立环境变量（不提交）
│   └── menu/                    #   餐厅智能助手（LangChain Agent + FastAPI）
│       ├── agent/               #   LangChain 餐厅 Agent（工具 + prompt + FAQ + milvus 同步）
│       ├── api/                 #   FastAPI 服务（SSE 流式 + REST）
│       ├── ui/                  #   Vue 3 + Vite + Element Plus 前端
│       ├── README.md            #   子项目文档
│       └── .env                 #   独立环境变量（不提交）
├── templates/                   # 全局模板目录
│   ├── minimall/                #   商城页面模板（base + partials）
│   └── admin/                   #   自定义 Admin 模板
├── sh/                          # 启动脚本
│   ├── deep_search_backend.sh   #   启动 deep_search 后端
│   ├── deep_search_frontend.sh  #   启动 deep_search 前端
│   ├── menu_backend.sh          #   启动 menu 后端
│   └── menu_frontend.sh         #   启动 menu 前端
├── demo/                        # [Demo] 自学教程代码（非业务，忽略）
│   ├── Base/                    #   Python 基础
│   ├── LangChain_v1.3/          #   LangChain 1.3 教程
│   ├── LangGraph_v1.2/          #   LangGraph 1.2 教程
│   ├── DeepAgent_v0.7/          #   DeepAgent 0.7 教程
│   ├── FastAPI/                 #   FastAPI 基础
│   └── SUMMARY.md               #   知识点学习总结（LangChain/LangGraph/DeepAgents）
├── .scratch/                    # 本地 Issue Tracker（Markdown，同步 GitHub，当前为空）
├── docs/
│   ├── agents/                  #   Agent 定义与 triage 规范
│   └── note/                    #   学习笔记（skills 分析等）
├── .claude/
│   ├── settings.json            #   Claude Code 权限与模型配置（不提交）
│   └── skills/                  #   自定义 skills（ai-radar / weekly-analyse-project / knowledge-interview-prep）
├── CLAUDE.md                    # 本文件（项目上下文）
├── CLAUDE_SYSTEM.md             # 系统级 CLAUDE.md 副本（参考用）
├── main.py                      # （空占位）
├── langgraph.json               # LangGraph CLI 配置（graph 指向 demo/LangGraph_v1.2）
├── pyproject.toml               # Ruff 配置（line-length=88, py313）
├── .pre-commit-config.yaml      # pre-commit hooks（ruff/codespell/conventional-commits）
├── .env                         # 环境变量（含 API Key，已加入 .gitignore）
├── .env.example                 # 环境变量模板（可安全提交）
├── requirements.txt             # 依赖列表
├── .gitignore
└── README.md                    # 项目简介 / 技术栈 / 模块 / 快速开始
```

---

## 4. 框架特定规范

### 4.1 Django

- **App 组织**：所有业务子应用统一创建在 `app/` 目录下（如 `app.minimall`），AppConfig 显式设置 `label` 固定为 app 名（保证表名/迁移记录不变）
- **Settings 拆分**：`base.py` 放通用配置，`dev.py` / `prod.py` 放环境特定配置；`manage.py` 默认加载 `charlotte_savanna.settings.dev`
- **数据库**：MySQL（`pymysql.install_as_MySQLdb()`），参数从环境变量读取（`DB_*`）
- **缓存**：Redis（`django-redis`），L2 缓存 + 击穿锁（SETNX/Pub-Sub）+ 熔断器；缓存设计与风险修复见 `app/minimall/todo/redis.md`
- **Models**：优先 `models.Model` 子类，字段显式命名，添加 `verbose_name`（中文项目）；树形结构使用 `django-mptt`（MPTTModel）
- **Views**：优先使用 CBV (Class-Based Views)，复杂逻辑抽取到 Service 层
- **URLs**：每个 app 维护自己的 `urls.py`（API 与页面分离：`urls_api.py` / `urls_html.py`），通过 `include()` 注册到根路由
- **Templates**：遵循 DRY，使用 `{% extends %}` / `{% include %}` 提取公共部分
- **Migrations**：每次模型变更生成 migration，提交到版本控制

### 4.2 LangChain / LangGraph / DeepAgents

- **Chain 构建**：优先使用 **LCEL (LangChain Expression Language)**，`|` 管道操作符优于 LLMChain（已弃用）
- **LangGraph**：使用 `StateGraph` + checkpointer（`InMemorySaver` / `PostgresSaver`）管理状态与记忆；LangGraph CLI 配置见 `langgraph.json`
- **DeepAgents**：使用 `create_deep_agent` 构建智能体，按职能拆分 subagent（数据库查询/网络搜索/知识库），配合中断（interrupt）实现 HITL
- **Tool 定义**：优先 `@tool` 装饰器，确保 `description` 清晰具体，能引导模型正确调用
- **Memory**：使用 `RunnableWithMessageHistory` + `BaseChatMessageHistory`，或 LangGraph checkpointer，按 `session_id`/`thread_id` 隔离会话
- **Embedding/RAG**：向量数据库做好持久化目录管理（`persist_directory`），`chunk_size` 和 `chunk_overlap` 根据文档类型调优

### 4.3 FastAPI / DeepAgents（deep_search 子项目）

- **服务启动**：`uvicorn` 运行 `project/deep_search/api/server.py`，配置 CORS 允许前端跨域
- **会话管理**：按 session/thread 上下文隔离（`api/context.py`），流式输出通过 WebSocket 推送（`utils/stream.py`）
- **外部服务**：MySQL 查询、RAGFlow 知识库、Tavily 网络搜索均封装为独立 tool，供 agent 调用
- **输出**：agent 生成 markdown，可转 PDF（`tools/pdf_tools.py`），落盘到 `output/`

### 4.4 前端（子项目 ui）

- **技术栈**：Vue 3 + Vite；deep_search 用 TypeScript、menu 用 JavaScript，组件库按需引入（如 Element Plus）
- **依赖管理**：`package.json` 明确声明 dependencies，`package-lock.json` 提交到版本控制
- **代码风格**：优先 `const`，组合式 API，箭头函数回调；TypeScript 项目开启严格模式

### 4.5 代码质量（Ruff / pre-commit）

- **Ruff**：配置在 `pyproject.toml`，`line-length = 88`，target `py313`；启用 isort 排序、flake8-simplify、pyupgrade 等规则
- **pre-commit**：`.pre-commit-config.yaml` 定义 hooks —— ruff（--fix + format）、codespell 拼写检查、conventional-commits 提交信息校验、trailing-whitespace / end-of-file 等基础检查
- **注释书写规范**（所有 Python 代码统一遵循）：
  - 中英混排：中文与英文单词/标识符之间留一个空格
  - 标点一律英文：注释中的逗号、句号、括号、冒号等使用 `,` `.` `(` `)` `:`，符号后按英文风格空一格
  - 行宽：代码与注释每行 ≤ 88 字符（Black 风格），超长必须换行，避免 pre-commit 的 ruff format 报错

### 4.6 文件组织

> 注释规范参见系统级 CLAUDE.md 第 6.2 节。

- **实验代码**：`demo/` 目录下的教程代码中，已注释的实现变体保留供学习参考，不要删除
- **Demo 命名**（仅适用于 `demo/` 目录）：
  - 教程按 `_序号_主题/` 目录组织（如 `demo/LangGraph_v1.2/_2_control_stream/`）
  - 文件按 `_模块_序号_描述.py` 命名（如 `_6_2_tool_node.py`），使用 `if __name__ == "__main__":` 包裹执行代码
  - Asset 文件：测试数据统一放在对应子目录的 `asset/` 或 `load/` 下

### 4.7 FastAPI / LangChain Agent（menu 子项目）

- **后端**：FastAPI（`api/main.py`）+ LangChain `create_agent`（`agent/langchain.py`），DeepSeek 模型，`@tool` 挂载三个工具（菜品查询 / 口味语义检索 / 餐位预订）
- **数据**：MySQL 存菜单与预订单，Milvus 存菜品向量做语义检索，Redis 存 FAQ 做相似推荐
- **初始化**：先执行 `agent/prompt/menu.sql`（建表）、`agent/milvus_sync.py`（向量库）、`agent/FAQ/redis_sync.py`（FAQ）再启动
- **前端**：Vue 3 + Element Plus（`ui/`），详细文档见 `project/menu/README.md`

---

## 5. 当前开发状态

> 当前各模块均为学习/测试性质，正式「主流程」尚未确定：minimall 为 Django 测试原型，deep_search / menu 为独立子项目。

### 5.1 测试原型 — minimall 商城 (`app/minimall/`)

| 组件 | 状态 | 说明 |
|------|------|------|
| Models | ✅ | 9 个模型，含 MPTT 分类树 |
| API (`views_buyer.py`) | ✅ | 认证/商品/购物车/地址/订单/充值等 DRF 接口 |
| 页面 (`views_html.py`) | ✅ | 商品列表/详情/购物车/下单/个人中心等 CBV 页面 |
| 缓存 (`cache.py`) | ✅ | Redis L2 缓存，覆盖击穿/穿透/雪崩/熔断 |
| 测试 (`tests/`) | ✅ | test_api / test_models / test_services |

### 5.2 子项目 — deep_search 智能体 (`project/deep_search/`)

| 组件 | 状态 | 说明 |
|------|------|------|
| Agent（main + subagents） | ✅ | DeepAgents + LangGraph，多 subagent 协作 |
| API (`api/server.py`) | ✅ | FastAPI 服务，WebSocket 流式输出 |
| Tools (`tools/`) | ✅ | MySQL/PDF/Markdown/RAGFlow/Tavily/文件读取 |
| RAGFlow 集成 | ✅ | `ragflow/demo.py` + 部署笔记 |
| 前端 (`ui/`) | ✅ | Vue 3 + Vite + TS |

> **全流程开发完毕**：agent → API → tools → RAGFlow → 前端链路已完整闭环，可通过 `sh/deep_search_backend.sh` + `sh/deep_search_frontend.sh` 启动。

### 5.3 子项目 — menu 智能助手 (`project/menu/`)

| 组件 | 状态 | 说明 |
|------|------|------|
| Agent (`agent/langchain.py`) | ✅ | LangChain create_agent + 3 个工具 |
| API (`api/main.py`) | ✅ | FastAPI，SSE 流式 + REST |
| 前端 (`ui/`) | ✅ | Vue 3 + Element Plus |

> 餐厅智能助手「一绪寿喜烧」已闭环，可通过 `sh/menu_backend.sh` + `sh/menu_frontend.sh` 启动。详细文档见 `project/menu/README.md`。

### 5.4 Demo 目录（仅供学习参考，不计入业务/子项目）

| 目录 | 状态 | 说明 |
|------|------|------|
| `demo/Base/` | ✅ 完成 | Python 基础教程 |
| `demo/LangChain_v1.3/` | ✅ 完成 | LangChain 1.3 教程 |
| `demo/LangGraph_v1.2/` | ✅ 完成 | LangGraph 1.2 教程 |
| `demo/DeepAgent_v0.7/` | ✅ 完成 | DeepAgent 0.7 教程 |
| `demo/FastAPI/` | ✅ 完成 | FastAPI 基础教程（3 个 demo） |
| `demo/SUMMARY.md` | ✅ 完成 | LangChain/LangGraph/DeepAgents 知识点学习总结 |

### 5.5 基础设施

| 项目 | 状态 | 说明 |
|------|------|------|
| .env 管理 | ✅ | `.env.example` 提供模板（Django/OpenAI/DeepSeek/Tavily/LangSmith/MySQL/PG/Redis/Milvus） |
| 数据库/缓存 | ✅ | MySQL + Redis（django-redis），配置环境变量化 |
| 代码质量 | ✅ | Ruff + pre-commit 已接入 |
| 依赖管理 | ✅ | `requirements.txt` 已生成（267 个包） |
| 配置安全 | ✅ | SECRET_KEY / ALLOWED_HOSTS / DEBUG 已环境变量化，settings 拆分 dev/prod |
| README | ✅ | 根 README 已补全（项目简介 / 技术栈 / 模块 / 快速开始） |

---

## 6. 项目注意事项

### 6.1 安全与配置

> 通用安全规范（`.env` 管理、API Key 保护、`.gitignore` 检查清单、敏感信息泄露处理）参见系统级 CLAUDE.md 第 3 节。

- 项目 `.env.example` 已提供所需环境变量模板
- `project/deep_search/.env`、`project/menu/.env` 为子项目独立环境变量，同样不提交
- 生产环境设置见 `settings/prod.py`（DEBUG=False + HSTS/HTTPS 加固），由 WSGI/ASGI 加载

### 6.2 业务工作范围（重要）

进行以下操作时，**工作范围限定在业务/子项目代码**，不涉及 `demo/` 目录（含 `demo/Base/`、`demo/LangChain_v1.3/`、`demo/LangGraph_v1.2/`、`demo/DeepAgent_v0.7/`、`demo/FastAPI/` 全部子目录）：

- 代码分析、搜索、重构
- Django app 开发
- 测试编写与运行
- 性能分析与优化
- 安全审计
- 依赖管理（`requirements.txt` 中仅业务/子项目需要的包）

如有疑问（如不确定某个文件是否属于业务/子项目代码），优先在 CLAUDE.md 中查看目录标注。

### 6.3 开发约定

- **虚拟环境**：`.venv/`，Windows Git Bash 下 `source .venv/Scripts/activate`
- **Django 启动**：`python manage.py runserver`（默认加载 `settings.dev`，依赖 MySQL + Redis）
- **deep_search 后端**：`sh/deep_search_backend.sh`（或 `python -m project.deep_search.api.server`）
- **deep_search 前端**：`sh/deep_search_frontend.sh`（或 `cd project/deep_search/ui && npm run dev`，首次需 `npm install`）
- **menu 后端**：`sh/menu_backend.sh`（或 `python -m project.menu.api.main`）
- **menu 前端**：`sh/menu_frontend.sh`（或 `cd project/menu/ui && npm run dev`，首次需 `npm install`）
- **LangGraph CLI**：`langgraph dev`（`langgraph.json` 配置了 graph 入口，指向 `demo/LangGraph_v1.2`）
- **LangChain 脚本**：在对应 `demo/` 子目录下 `python <script>.py`（脚本内部 `load_dotenv()`）
- **实验性代码**：教程文件中的注释代码刻意保留，展示不同实现变体
- **协作**：接受 PR（历史中有从 `szh1007` 的多分支合并），分支命名如 `YYYYMMDD`

### 6.4 依赖管理

```bash
pip install -r requirements.txt    # 安装
pip freeze > requirements.txt      # 更新
# 子项目前端（独立）：
cd project/deep_search/ui && npm install
cd project/menu/ui && npm install
```

核心依赖：Django 6.0、DRF、LangChain/LangGraph 1.x、DeepAgents、FastAPI、PyMySQL、SQLAlchemy、django-redis、redis-py、ChromaDB、FAISS、RAGFlow SDK、Milvus、python-dotenv、Tavily

### 6.5 Claude Code 说明

- 模型后端：DeepSeek（Anthropic 兼容模式），配置在 `settings.json`
- `.claude/CLAUDE.md` 可提交，`settings.json` 不提交（含个人 API Key）
- 项目自定义 skills 在 `.claude/skills/`（`ai-radar` AI 领域周报、`weekly-analyse-project` 项目周报、`knowledge-interview-prep` 知识点总结与面试题整理）
- 系统级通用规范在 `~/.claude/CLAUDE.md`

### 6.6 文档同步维护（CLAUDE.md / README.md）

当项目文件发生大幅度变更（新增/删除模块、重构、新增重要文档或 skill 等）时，需同步更新 `CLAUDE.md` 与 `README.md`，保持文档与代码一致。

- **定期询问**：每周六晚 21:00 主动询问用户「是否大幅度更改了项目文件内容，是否需要同步更新 CLAUDE.md 与 README.md」。
- **上次更新时间**：记录在本文档末尾「最后更新」字段，每次同步更新文档后刷新该日期。
- **超期提醒**：若距离上次更新超过 7 天，在会话开始时提醒一次「是否需要更新 CLAUDE.md 与 README.md」。

---

## 7. Agent skills

### Issue tracker

本地 Markdown（`.scratch/<feature-slug>/`），随仓库提交同步到 GitHub。详见 `docs/agents/issue-tracker.md`。

### Triage labels

使用默认标签名：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文（single-context）：待创建 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。

---

> **最后更新**：2026-08-17 | **维护者**：Claude Code (charlotte)
