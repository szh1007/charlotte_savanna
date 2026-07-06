# CLAUDE.md — charlotte_savanna

> Claude Code 项目上下文文件。本文档由 Claude 维护，记录项目结构、编码规范和注意事项，供后续会话自动加载。

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
| **包管理** | pip + venv（无 requirements.txt/pyproject.toml） | — |
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
├── .gitignore
├── .claude/
│   ├── settings.json            # Claude Code 权限与模型配置
│   └── CLAUDE.md                # 本文件
└── README.md                    # 仅含标题 "# charlotte_savanna"
```

---

## 4. 编码规范

### 4.1 Python 通用规范 (PEP 8 + Google Python Style Guide)

**代码风格：**
- 缩进：4 空格，禁止 Tab
- 行宽：120 字符（Django 项目惯例，非严格的 79）
- 文件编码：UTF-8
- 换行符：Unix `\n`
- 文件末尾：有且仅有一个空行

**命名约定 (PEP 8)：**
| 类型 | 规则 | 示例 |
|------|------|------|
| 模块/文件 | `snake_case` | `document_loader.py` |
| 类 | `PascalCase` | `CharlotteConfig`, `RecursiveCharacterTextSplitter` |
| 函数/方法 | `snake_case` | `get_session_history()` |
| 变量 | `snake_case` | `chat_model`, `embedding_model` |
| 常量 | `UPPER_SNAKE_CASE` | `MAX_AGE`, `BASE_DIR` |
| 私有成员 | `_leading_underscore` | `_gender`, `__age` (name mangling) |

**类型注解 (PEP 484 / PEP 604)：**
```python
# ✅ 推荐：使用 Python 3.10+ 联合类型语法
def test_tool(query: str) -> str:
    ...

def get_session_history(session_id: str) -> BaseChatMessageHistory:
    ...

# ✅ 推荐：使用 `|` 替代 `Optional` / `Union`
def process(data: dict[str, int] | None = None) -> list[str]:
    ...
```

**文档字符串 (PEP 257 + Google Style)：**
```python
def load_documents(path: str, encoding: str = "utf-8") -> list[Document]:
    """加载指定路径的文档。

    Args:
        path: 文档文件路径。
        encoding: 文件编码，默认 utf-8。

    Returns:
        加载后的 Document 对象列表。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
    """
    ...
```

**Import 顺序 (isort 风格)：**
1. 标准库 (`import os, sys`)
2. 第三方库 (`from django.conf import settings`)
3. 本地模块 (`from charlotte.models import ...`)

每组之间空一行，按字母序排列。

### 4.2 Django 规范

- **Models**：优先使用 `models.Model` 的子类，字段显式命名，添加 `verbose_name`（中文项目）
- **Views**：优先使用 CBV (Class-Based Views)，复杂逻辑抽取到 Service 层
- **URLs**：每个 app 维护自己的 `urls.py`，通过 `include()` 注册到根路由
- **Settings**：敏感配置通过 `os.environ.get()` 读取，不硬编码
- **Migrations**：每次模型变更生成 migration，提交到版本控制
- **Templates**：遵循 DRY 原则，使用 `{% extends %}` / `{% include %}` 提取公共部分

### 4.3 LangChain 规范

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

### 4.4 Git 规范 (Conventional Commits)

```
<type>: <description>

feat: 新功能
fix:  修复 bug
docs: 文档变更
style: 代码格式（不影响功能）
refactor: 重构
test: 测试相关
chore: 构建/工具变更
clean: 清理代码

示例:
feat: LangChain RAG 1
feat: demo class
clean: cached & apply gitignore
```

### 4.5 文件组织规范

- **Demo/Tutorial 文件**：按 `序号_描述.py` 命名 (如 `1_1_LCEL.py`)，使用 `if __name__ == "__main__":` 包裹执行代码
- **注释**：中文注释，说明"为什么"而非"是什么"
- **实验代码**：保留已注释的实现变体供学习参考，不要删除
- **Asset 文件**：测试数据统一放在 `asset/` 子目录

### 4.6 安全规范

- **绝不提交** `.env` 文件（已在 `.gitignore` 中）
- **绝不硬编码** API Key、Token、密码等敏感信息 — 一律通过 `os.environ.get()` 读取，配置模板写入 `.env.example`
- **Django SECRET_KEY** 已改为从 `DJANGO_SECRET_KEY` 环境变量读取，本地开发提供默认 fallback
- **DEBUG** 已改为从 `DJANGO_DEBUG` 环境变量读取，默认为 `True`（开发），生产环境显式设为 `False`
- **ALLOWED_HOSTS** 已改为从 `DJANGO_ALLOWED_HOSTS` 环境变量读取
- 提交前检查是否有注释掉的 API Key 残留（已清理 `1_模型的调用.py` 中的演示 Key）

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
| .env 管理 | ✅ | 使用 python-dotenv，`.env.example` 已提供模板 |
| .gitignore | ✅ | 覆盖 Python/Django/IDE/OS 常见排除项 |
| 依赖管理 | ✅ | `requirements.txt` 已生成（177 个包） |
| 配置安全 | ✅ | SECRET_KEY / DEBUG / ALLOWED_HOSTS 已环境变量化 |
| README | ⚠️ | 仅一行标题，需要补充完整文档 |
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

## 6. 注意事项

### 6.1 重要提醒

1. **`.claude/CLAUDE.md` 会提交到仓库**，供所有协作者共享项目上下文。`.claude/settings.json` 包含个人 API 配置，已在 `.gitignore` 中排除。
2. **`.env` 文件包含真实的 API Key**，已加入 `.gitignore`。`.env.example` 可作为模板参考，可安全提交到仓库。
3. **Django 配置已环境变量化** — `SECRET_KEY`、`DEBUG`、`ALLOWED_HOSTS` 均从环境变量读取，本地开发有默认 fallback 值，生产环境需在 `.env` 中显式设置。
4. **`DEBUG=False`** 是生产环境的必要条件，通过 `DJANGO_DEBUG=False` 设置。

### 6.2 开发约定

- **虚拟环境路径**：`.venv/`，使用 `source .venv/Scripts/activate` 激活（Windows Git Bash）
- **Django 启动**：`python manage.py runserver`
- **LangChain 脚本**：直接在 `LangChain/` 子目录下 `python <script>.py` 运行（脚本内部会 `load_dotenv()`）
- **实验性代码**：教程文件中的注释代码是刻意保留的，展示了不同的实现变体，供学习参考
- **协作模式**：该仓库接受 PR（历史中有从 `szh1007` 的多个 PR 合并），新的功能分支命名如 `20260XXX`

### 6.3 依赖管理

```bash
# 安装依赖
pip install -r requirements.txt

# 更新依赖文件
pip freeze > requirements.txt
```

主要依赖包：
- Django >= 6.0
- langchain-core, langchain-classic, langchain-community, langchain-openai
- langchain-text-splitters, langchain-experimental, langchain-chroma
- langchain-tavily, langsmith
- openai, chromadb, faiss-cpu
- python-dotenv, tiktoken, numpy

### 6.4 Claude Code 特定说明

- 模型后端为 DeepSeek（Anthropic 兼容模式），配置在 `.claude/settings.json`
- 语言设置为中文，所有交互和代码注释使用中文
- 权限已配置 allow/deny 列表，详见 `settings.json`
- 使用前需确保 `.env` 中的 `ANTHROPIC_AUTH_TOKEN` 有效

---

## 7. Claude 沟通与回复规范

> 参考 SOTA 标准：Google Technical Writing Course、BLUF (Bottom Line Up Front)、Conventional Comments。

### 7.1 语言规则

- **中文回复**：解释、分析、讨论默认用中文
- **英文保留**：代码标识符、CLI 命令、变量名、函数名、文件路径、技术术语保持原样不翻译
  ```
  # ✅ 正确
  在 `charlotte_savanna/settings.py` 中把 `DEBUG` 设为 `False`

  # ❌ 错误
  在 夏洛特草原/设置.派 中把 调试 设为 假
  ```

### 7.2 回复结构（BLUF 原则）

- **结论先行**，先给答案/操作，再给理由和细节
- **禁止铺垫**：不先解释背景、不讲"在开始之前..."、"首先了解一下..."
- 大段内容用表格或列表呈现，而非长段落
- 代码块注明语言和文件路径

### 7.3 沟通风格

| 禁止 | 替代方式 |
|------|----------|
| "这是个很好的问题" | 直接回答问题 |
| "当然可以" | 直接执行 |
| "让我帮你..." | 直接给出方案 |
| "太棒了/非常好" | 陈述事实 |
| "我想/我觉得/我认为" | 直接给出判断，不拖泥带水 |
| 过度道歉 | 指出问题和修正方案即可 |

**原则：**
- 给真实判断 — 方案有缺陷直接指出，不做无意义认可
- 发现问题主动说明 — 不管是否在用户关心的范围内
- 提供更好的替代方案 — 如果知道更优做法，直接提出并说明理由
- 不猜测用户的情绪或意图 — 基于事实和数据回复
- 不确定时明确说 "不确定"，不要编造

### 7.4 代码交互规范

- 修改前先阅读文件，确保上下文正确
- 生成代码匹配项目现有风格，不引入不一致的格式
- 命名和项目惯用 idiom 保持一致
- 多文件修改时先展示变更范围，再逐文件实施

---

## 8. Git 操作规范

> 参考 SOTA 标准：Conventional Commits 1.0.0、GitHub Flow、Atomic Commits。

### 8.1 提交权限

- **不自动 commit / push** — 除非用户明确提出（如"提交"、"push"、"提交这次变更"）
- 用户说"修改/更新/修复"时不等于要求提交
- 如果变更范围较大且用户要求提交，先展示变更摘要待确认

### 8.2 提交前流程

1. 展示将要提交的文件列表和变更统计
2. 列出核心变更点（每条一行，中文说明）
3. 用户确认后执行 `git add` + `git commit`

示例：
```
待提交文件:
  charlotte_savanna/settings.py  (+5 -3)
  .env.example                   (new file)
  requirements.txt               (new file)

变更摘要:
  - SECRET_KEY / DEBUG / ALLOWED_HOSTS 从环境变量读取
  - 新增 .env.example 环境变量模板
  - 生成 requirements.txt 依赖文件
```

### 8.3 Commit Message 规范

遵循 Conventional Commits，使用简洁英文：

```
<type>: <imperative description>

feat: add user authentication
fix: resolve null pointer in form validation
refactor: extract common query logic
docs: update API documentation
chore: update dependencies

# 允许少量中文，仅当英文无法简洁表达时：
feat: add 用户权限管理 module
```

- 首行 ≤ 72 字符
- 使用祈使语气（"add" 而非 "added"）
- 不需要句末标点

### 8.4 分支与合并

- 当前分支 `master` 为主分支
- 新功能使用特性分支，命名 `YYYYMMDD` 或 `<type>/<description>`
- Push 前确保本地测试通过
- 禁止 `--force` push 到 `master`

### 8.5 安全红线

- `.env` 文件绝对不能提交（已在 `.gitignore` 中）
- 提交前用 `git diff --staged` 检查是否包含敏感信息
- 如果意外提交了敏感信息，立即 `git reset` 并通知用户

---

> **最后更新**：2026-07-06 | **维护者**：Claude Code (charlotte)
