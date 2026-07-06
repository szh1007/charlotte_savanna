# CLAUDE.md — charlotte_savanna

> 通用规范（沟通风格、Git 操作、Python 编码、安全原则）参见系统级 `~/.claude/CLAUDE.md`

> 本文档仅包含 charlotte_savanna 项目特定内容。

---

## 1. 项目概述

**charlotte_savanna** 是一个个人学习型项目，以 **Django 6.0** 为骨架，围绕两大主线展开：

| 主线 | 位置 | 说明 |
|------|------|------|
| **Python 语言基础** | `charlotte/demo/` | OOP、装饰器、迭代器/生成器、深拷贝、多进程/多线程/协程 |
| **LangChain 框架** | `LangChain/` | 从 Model I/O → Chain → Memory → Tools → Agent → RAG 的完整渐进式教程 |

项目初始化于 2026-04-27，当前处于活跃开发中。Django 部分目前仅有脚手架，无实际业务模型和视图。

---

## 2. 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| **语言** | Python | 3.13.13 |
| **Web 框架** | Django | 6.0.4 |
| **数据库** | SQLite3（开发） | — |
| **LLM 框架** | LangChain (`langchain_core`, `langchain_classic`, `langchain_community`, `langchain_openai`, `langchain_text_splitters`, `langchain_experimental`, `langchain_chroma`) | 最新 |
| **LLM 提供商** | OpenAI (via proxy `api.openai-proxy.org`) + DeepSeek (via `api.deepseek.com`) | — |
| **向量数据库** | ChromaDB / FAISS | — |
| **嵌入模型** | `text-embedding-3-large` / `text-embedding-ada-002` | — |
| **对话模型** | `gpt-4o-mini` (OpenAI) / `deepseek-v4-pro` (DeepSeek) | — |
| **搜索工具** | Tavily Search (`langchain_tavily`) | — |
| **HTTP/Async** | httpx, aiohttp, uvicorn | — |
| **环境管理** | python-dotenv (.env) | — |
| **包管理** | pip + venv | — |
| **IDE** | PyCharm / IntelliJ IDEA (`.idea/`) | — |
| **AI 助手** | Claude Code (`.claude/`) | — |

**LLM API 配置说明：**
- **OpenAI 代理**：`OPENAI_BASE_URL="https://api.openai-proxy.org/v1"` — LangChain 教程中调用 `gpt-4o-mini` 等模型
- **DeepSeek**：`ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"` — Claude Code 的 Anthropic 兼容后端

---

## 3. 项目结构

```
charlotte_savanna/
├── manage.py                    # Django CLI 入口
├── charlotte_savanna/           # Django 项目配置包
│   ├── __init__.py
│   ├── settings.py              # Django 6.0 设置 (DEBUG=True, SQLite)
│   ├── urls.py                  # 根路由 (当前仅 /admin/)
│   ├── wsgi.py / asgi.py        # 部署入口
│   └── ...
├── charlotte/                   # 主 Django App
│   ├── apps.py                  # AppConfig: CharlotteConfig
│   ├── models.py                # （空）
│   ├── views.py                 # （空）
│   ├── admin.py                 # （空）
│   ├── tests.py                 # 测试文件
│   ├── migrations/              # Django 迁移目录
│   └── demo/                    # Python 语言基础教程
│       ├── class.py             #   抽象类、继承、property、魔术方法
│       ├── decorator.py         #   闭包、函数/类装饰器
│       ├── deepcopy.py          #   深拷贝
│       ├── iterator.py          #   迭代器模式
│       ├── generator.py         #   生成器 (yield/send/yield from)
│       └── process/             #   多进程/多线程/协程
│           ├── 1_*_process*.py  #     进程定义、Pool、通信(Queue/Pipe)
│           ├── 3_thread.py      #     线程、RLock
│           └── 6_2_coroutine.py #     asyncio、aiohttp
├── LangChain/                   # LangChain 渐进式教程
│   ├── demo.py                  #   ★ 集大成示例：Agent + RAG + Memory + Tools
│   ├── 1_Model_IO/              #   模型调用、Prompt 模板、输出解析器、Ollama
│   ├── 2_Chain/                 #   LCEL (推荐)、LLMChain
│   ├── 3_Memory/                #   对话记忆 (Buffer/Window/Token/Summary/Entity/KG)
│   ├── 4_Tools/                 #   工具定义 (@tool) 与调用
│   ├── 5_Agent/                 #   Agent (ReAct + Function Calling)
│   └── 6_RAG/                   #   文档加载→拆分→嵌入→检索 完整管线
│       ├── 1_loader.py          #     TextLoader/PyPDFLoader/CSVLoader/JSONLoader
│       ├── 2_splitter.py        #     Character/Recursive/Token/Semantic
│       ├── 3_embed.py           #     ChromaDB 持久化与检索
│       └── 4_retriever.py       #     FAISS 检索器
├── templates/                   # Django 模板目录（空）
├── .env                         # 环境变量（含 API Key，已加入 .gitignore）
├── .env.example                 # 环境变量模板（可安全提交）
├── requirements.txt             # 依赖列表
├── .gitignore
├── .claude/
│   ├── settings.json            # Claude Code 权限与模型配置（不提交）
│   └── CLAUDE.md                # 本文件（提交）
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

### 4.3 文件组织

- **Demo/Tutorial 文件**：按 `序号_描述.py` 命名 (如 `1_1_LCEL.py`)，使用 `if __name__ == "__main__":` 包裹执行代码
- **注释**：中文注释，说明"为什么"而非"是什么"
- **实验代码**：保留已注释的实现变体供学习参考，不要删除
- **Asset 文件**：测试数据统一放在 `asset/` 子目录

---

## 5. 当前开发状态

### 5.1 Django App (`charlotte/`)

| 组件 | 状态 | 说明 |
|------|------|------|
| models.py | 🚧 空 | 无数据模型 |
| views.py | 🚧 空 | 无视图 |
| admin.py | 🚧 空 | 无管理后台注册 |
| urls.py | 🚧 不存在 | App 内无路由配置 |
| templates/ | 🚧 空 | 无模板文件 |
| tests.py | 🚧 空 | 无测试用例 |
| demo/ | ✅ 完成 | Python 基础教程完整 |

### 5.2 LangChain 教程 (`LangChain/`)

| 模块 | 状态 | 说明 |
|------|------|------|
| 1_Model_IO | ✅ 完成 | 5 个示例文件 |
| 2_Chain | ✅ 完成 | 3 个示例文件 |
| 3_Memory | ✅ 完成 | 1 个综合文件 |
| 4_Tools | ✅ 完成 | 2 个示例文件 |
| 5_Agent | ✅ 完成 | 2 个示例文件 (新旧模式) |
| 6_RAG | ✅ 完成 | 4 个管线步骤 + asset 数据 |
| demo.py | ✅ 完成 | 集成示例 (Agent + RAG + Memory) |

### 5.3 基础设施

| 项目 | 状态 | 说明 |
|------|------|------|
| .env 管理 | ✅ | `.env.example` 提供模板 |
| .gitignore | ✅ | 覆盖 Python/Django/IDE/OS 常见排除项 |
| 依赖管理 | ✅ | `requirements.txt` 已生成（177 个包） |
| 配置安全 | ✅ | SECRET_KEY / DEBUG / ALLOWED_HOSTS 已环境变量化 |
| README | ⚠️ | 仅一行标题，需补充完整文档 |
| 测试 | ❌ | 无任何测试覆盖 |

### 5.4 近期提交历史

```
c8113e8 feat: Init Claude            ← 最新：Claude Code 集成
39739cf feat: LangChain demo         ← Agent 集成示例
683b23f feat: LangChain RAG 2
6e4413c feat: LangChain RAG 1
40ddd41 feat: LangChain Agent 2
84a0d23 feat: LangChain Agent 1
e8cf37c feat: LangChain Tools
...
fed40ff Initial Project              ← 2026-04-27
```

---

## 6. 项目注意事项

### 6.1 安全与配置

- **`.env` 已加入 `.gitignore`**，`.env.example` 作为模板提交到仓库
- **`SECRET_KEY`** / **`DEBUG`** / **`ALLOWED_HOSTS`** 已环境变量化，本地有默认 fallback
- **生产部署**：在 `.env` 中设置 `DJANGO_DEBUG=False` + 强随机 `DJANGO_SECRET_KEY`
- **API Key**：`1_模型的调用.py` 注释中的演示 Key 已替换为占位符，不要将新 Key 写入源码注释

### 6.2 开发约定

- **虚拟环境**：`.venv/`，Windows Git Bash 下 `source .venv/Scripts/activate`
- **Django 启动**：`python manage.py runserver`
- **LangChain 脚本**：在 `LangChain/` 子目录下 `python <script>.py`（脚本内部 `load_dotenv()`）
- **实验性代码**：教程文件中的注释代码刻意保留，展示不同实现变体
- **协作**：接受 PR（历史中有从 `szh1007` 的多分支合并），分支命名如 `YYYYMMDD`

### 6.3 依赖管理

```bash
pip install -r requirements.txt    # 安装
pip freeze > requirements.txt      # 更新
```

核心依赖：Django ≥ 6.0、LangChain 全家桶、OpenAI SDK、ChromaDB、FAISS、python-dotenv、Tavily

### 6.4 Claude Code 说明

- 模型后端：DeepSeek（Anthropic 兼容模式），配置在 `settings.json`
- `.claude/CLAUDE.md` 可提交，`settings.json` 不提交（含个人 API Key）
- 系统级通用规范在 `~/.claude/CLAUDE.md`

---

> **最后更新**：2026-07-06 | **维护者**：Claude Code (charlotte)
