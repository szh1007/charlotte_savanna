# CLAUDE.md — charlotte_savanna

> 通用规范（沟通风格、Git 操作、Python 编码、安全原则）参见系统级 `~/.claude/CLAUDE.md`

> 本文档仅包含 charlotte_savanna 项目特定内容。

---

## 1. 项目概述

**charlotte_savanna** 是一个以 **Django 6.0** 为骨架的个人技术学习项目，用于自学 Django 开发、LangChain、Node.js 等技术栈。

项目初始化于 2026-04-27，当前处于活跃开发中。项目整体方向尚未确定，后续可能新建多个 Django app 用于不同技术方向的学习和实验。

---

## 1.1 Demo 目录（非主流程，分析/开发时请忽略）

根目录 `demo/` 下是个人自学 demo 和测试代码，**不属于项目主流程**。分析代码、重构、写测试、排查问题时均应跳过整个 `demo/` 目录：

| 目录 | 内容 | 说明 |
|------|------|------|
| `demo/Python/` | Python OOP、装饰器、迭代器/生成器、深拷贝、多进程/多线程/协程 | 自学测试，与业务无关 |
| `demo/LangChain/` | LangChain 渐进式教程 (Model I/O → Agent → RAG) | 自学测试，与业务无关 |
| `demo/Claude/` | Node.js Express 渐进式教程 (HelloWorld → Payment → ...) | 自学测试，与业务无关 |

这些目录仅作为个人学习参考保留，后续不会被删除。**所有自学测试用 demo 统一放入 `demo/` 目录**。任何主流程相关的工作（model 设计、view 编写、测试、性能分析、安全审计等）一律不涉及 `demo/` 目录。

---

## 2. 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| **语言** | Python | 3.13 |
| **Web 框架** | Django | 6.0 |
| **数据库** | SQLite3（开发） | — |
| **LLM 框架** | LangChain 全家桶 | 最新 |
| **向量数据库** | ChromaDB / FAISS | — |
| **Node.js 运行时** | Node.js + Express 4.x | — |
| **包管理** | pip + venv + npm | — |
| **环境管理** | python-dotenv (.env) | — |
| **AI 助手** | Claude Code (`.claude/`) | — |

> LLM 提供商、具体模型名、API 端点和 Key 配置参见 `.env.example`。具体模型列表不在本文档中维护，避免过时。

---

## 3. 项目结构

```
charlotte_savanna/
├── manage.py                    # Django CLI 入口
├── charlotte_savanna/           # Django 项目配置包
│   ├── __init__.py
│   ├── settings.py              # Django 6.0 设置 (DEBUG=True, SQLite)
│   ├── urls.py                  # 根路由 (当前仅 /admin/)
│   ├── wsgi.py                  # WSGI 部署入口
│   ├── asgi.py                  # ASGI 部署入口
│   └── ...
├── charlotte/                   # 主 Django App（待确定方向）
│   ├── __init__.py
│   ├── apps.py                  # AppConfig: CharlotteConfig
│   ├── models.py                # （空）
│   ├── views.py                 # （空）
│   ├── admin.py                 # （空）
│   ├── tests.py                 # 测试文件
│   └── migrations/              # Django 迁移目录
├── demo/                        # [Demo] 自学测试代码（非主流程，忽略）
│   ├── Python/                  #   Python 基础教程（class/decorator/iterator/generator/process）
│   ├── LangChain/               #   LangChain 渐进式教程（Model I/O → Agent → RAG）
│   └── Claude/                  #   Node.js Express 渐进式教程（HelloWorld → Payment）
├── templates/                   # Django 模板目录（空）
├── docs/                        # 项目文档
│   ├── agents/                  #   Agent 定义与 triage 规范
│   └── discuss/                 #   讨论记录
├── CLAUDE.md                    # 本文件（项目上下文）
├── CLAUDE_SYSTEM.md             # 系统级 CLAUDE.md 副本（参考用）
├── main.py                      # （空占位）
├── .env                         # 环境变量（含 API Key，已加入 .gitignore）
├── .env.example                 # 环境变量模板（可安全提交）
├── requirements.txt             # 依赖列表
├── .gitignore
├── .claude/
│   ├── settings.json            # Claude Code 权限与模型配置（不提交）
│   ├── commands/                #   自定义 slash 命令
│   └── skills/                  #   自定义 skills
└── README.md                    # 仅含标题 "# charlotte_savanna"
```

---

## 4. 框架特定规范

### 4.1 Django 规范

- **Models**：优先使用 `models.Model` 的子类，字段显式命名，添加 `verbose_name`（中文项目）
- **Views**：优先使用 CBV (Class-Based Views)，复杂逻辑抽取到 Service 层
- **URLs**：每个 app 维护自己的 `urls.py`，通过 `include()` 注册到根路由
- **Settings**：敏感配置通过 `os.environ.get()` 读取，不硬编码；开发默认值允许 fallback
- **Migrations**：每次模型变更生成 migration，提交到版本控制
- **Templates**：遵循 DRY 原则，使用 `{% extends %}` / `{% include %}` 提取公共部分

### 4.2 LangChain 规范

- **Chain 构建**：优先使用 **LCEL (LangChain Expression Language)**，`|` 管道操作符优于 LLMChain
  ```python
  # ✅ 推荐：LCEL
  chain = prompt | llm | output_parser

  # ❌ 避免：旧式 LLMChain（已弃用）
  chain = LLMChain(llm=llm, prompt=prompt)
  ```
- **Agent 模式**：优先使用 `create_tool_calling_agent` (Function Calling)，次选 `create_react_agent`
- **Tool 定义**：优先使用 `@tool` 装饰器，确保 `description` 清晰、具体，能引导模型正确调用
- **Memory**：使用 `RunnableWithMessageHistory` + `BaseChatMessageHistory`，按 `session_id` 隔离会话
- **Embedding/RAG**：向量数据库做好持久化目录管理 (`persist_directory`)，`chunk_size` 和 `chunk_overlap` 根据文档类型调优

### 4.3 Node.js / Express 规范

- **模块系统**：使用 CommonJS (`require`/`module.exports`)，与 Node.js 生态默认一致
- **异步处理**：优先使用 `async/await`，避免 callback hell
- **错误处理**：Express 路由中使用 try/catch 或 wrapper，不静默吞异常
- **依赖管理**：`package.json` 明确声明 `dependencies`，`package-lock.json` 提交到版本控制
- **端口配置**：通过环境变量 `PORT` 读取，提供默认 fallback
- **代码风格**：2 空格缩进，使用 `const` 优先，箭头函数回调

### 4.4 文件组织

> 注释规范参见系统级 CLAUDE.md 第 6.2 节。

- **实验代码**：`demo/` 目录下的教程代码中，已注释的实现变体保留供学习参考，不要删除
- **Demo 文件命名**（仅适用于 `demo/` 目录）：
  - Python：按 `序号_描述.py` 命名 (如 `1_1_LCEL.py`)，使用 `if __name__ == "__main__":` 包裹执行代码
  - Node.js：按 `序号_描述/` 目录组织 (如 `1_HelloWorld/`)，入口文件为 `server.js` 或 `index.js`
  - Asset 文件：测试数据统一放在对应 demo 子目录的 `asset/` 下

---

## 5. 当前开发状态

### 5.1 主流程 — Django App (`charlotte/`)

| 组件 | 状态 | 说明 |
|------|------|------|
| models.py | 🚧 待实现 | 业务模型（方向待定） |
| views.py | 🚧 待实现 | 视图逻辑（方向待定） |
| admin.py | 🚧 待实现 | Django Admin 注册 |
| urls.py | 🚧 待实现 | App 路由配置 |
| templates/ | 🚧 待实现 | 前端模板 |
| tests.py | 🚧 待实现 | 单元测试与集成测试 |

### 5.2 Demo 目录（仅供学习参考，不计入主流程）

| 目录 | 状态 | 说明 |
|------|------|------|
| `demo/Python/` | ✅ 完成 | Python 基础教程（OOP、装饰器、迭代器、多进程等） |
| `demo/LangChain/` | ✅ 完成 | LangChain 渐进式教程（Model I/O → Agent → RAG，7 个模块） |
| `demo/Claude/` | ✅ 完成 | Node.js Express 渐进式教程（HelloWorld + Payment） |

### 5.3 基础设施

| 项目 | 状态 | 说明 |
|------|------|------|
| .env 管理 | ✅ | `.env.example` 提供模板 |
| .gitignore | ✅ | 覆盖 Python/Django/Node.js/IDE/OS 常见排除项 |
| 依赖管理 | ✅ | `requirements.txt` 已生成（177 个包） |
| 配置安全 | ✅ | SECRET_KEY / DEBUG / ALLOWED_HOSTS 已环境变量化 |
| README | ⚠️ | 仅一行标题，需补充完整文档 |
| 测试 | ❌ | 无任何测试覆盖 |

### 5.4 近期提交历史

```
992f634 feat: add Node.js HelloWorld Express server  ← 最新
fb8a959 fix: improve CLAUDE.md
9dda83a fix: 完善 CLAUDE.md
6cdc0a0 fix: 拆分系统级 CLAUDE.md
ee7bb5b add .claude/CLAUDE.md
48ff873 feat: 新增 claude.md
c8113e8 feat: Init Claude
39739cf feat: LangChain demo
...
fed40ff Initial Project              ← 2026-04-27
```

---

## 6. 项目注意事项

### 6.1 安全与配置

> 通用安全规范（`.env` 管理、API Key 保护、`.gitignore` 检查清单、敏感信息泄露处理）参见系统级 CLAUDE.md 第 3 节。
> 项目 `.env.example` 已提供所需环境变量模板。

### 6.2 主流程工作范围（重要）

进行以下操作时，**工作范围限定在主流程代码**，不涉及 `demo/` 目录（含 `demo/Python/`、`demo/LangChain/`、`demo/Claude/` 三个子目录）：

- 代码分析、搜索、重构
- Django app 开发
- 测试编写与运行
- 性能分析与优化
- 安全审计
- 依赖管理（`requirements.txt` 中仅主流程需要的包）

如有疑问（如不确定某个文件是否属于主流程），优先在 CLAUDE.md 中查看目录标注。

### 6.3 开发约定

- **虚拟环境**：`.venv/`，Windows Git Bash 下 `source .venv/Scripts/activate`
- **Django 启动**：`python manage.py runserver`
- **LangChain 脚本**：在 `demo/LangChain/` 子目录下 `python <script>.py`（脚本内部 `load_dotenv()`）
- **Node.js 脚本**：在 `demo/Claude/` 子目录下 `npm start` 或 `node server.js`（首次需 `npm install`）
- **实验性代码**：教程文件中的注释代码刻意保留，展示不同实现变体
- **协作**：接受 PR（历史中有从 `szh1007` 的多分支合并），分支命名如 `YYYYMMDD`

### 6.4 依赖管理

```bash
pip install -r requirements.txt    # 安装
pip freeze > requirements.txt      # 更新
```

核心依赖：Django ≥ 6.0、LangChain 全家桶、OpenAI SDK、ChromaDB、FAISS、python-dotenv、Tavily

### 6.5 Claude Code 说明

- 模型后端：DeepSeek（Anthropic 兼容模式），配置在 `settings.json`
- `.claude/CLAUDE.md` 可提交，`settings.json` 不提交（含个人 API Key）
- 系统级通用规范在 `~/.claude/CLAUDE.md`

---

---

## 7. Agent skills

### Issue tracker

本地 Markdown（`.scratch/<feature-slug>/`），不自动同步 GitHub，由用户自行提交推送。详见 `docs/agents/issue-tracker.md`。

### Triage labels

使用默认标签名：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文（single-context）：待创建 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。

---

> **最后更新**：2026-07-07 | **维护者**：Claude Code (charlotte)
