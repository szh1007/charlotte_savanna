# 深度检索智能体（Deep Search Agent）

> 基于 **DeepAgents + LangGraph + FastAPI + Vue 3** 的企业级深度检索智能体。
> 主智能体协调多个专家子智能体（网络搜索 / 数据库查询 / 知识库检索），完成信息收集与文档生成。

---

## 一、项目简介

`project/deep_search` 以「沃华医药」业务为背景，实现一个主智能体 + 三个专家子智能体的协作系统：

- **主智能体**：负责任务规划、信息汇总与文档生成（Markdown → PDF）。
- **子智能体**：网络搜索助手（Tavily）、数据库查询助手（MySQL）、RAGFlow 知识库助手。
- **实时交互**：FastAPI WebSocket 流式上报工具调用、子智能体委派、任务进度与最终结果。
- **会话隔离**：基于 `ContextVar` 的协程级上下文隔离 + `InMemorySaver` 按 `thread_id` 记忆。

---

## 二、目录结构

```
project/deep_search/
├── __init__.py
├── .env                          # 独立环境变量（不提交，含 API Key）
├── agent/                        # 智能体定义
│   ├── llm.py                    # 模型初始化（DeepSeek）
│   ├── main_agent.py             # 主智能体（create_deep_agent）
│   ├── prompt.py                 # 加载提示词配置
│   └── subagents/                # 子智能体
│       ├── network_search_agent.py   # 网络搜索助手
│       ├── database_query_agent.py   # 数据库查询助手
│       └── kownledge_base_agent.py   # RAGFlow 知识库助手
├── api/                          # FastAPI 服务
│   ├── server.py                 # REST + WebSocket 流式接口
│   ├── context.py                # ContextVar 会话上下文隔离
│   └── monitor.py                # 工具进度监控（单例）
├── tools/                        # Agent 工具
│   ├── tavily_tool.py            # 联网搜索
│   ├── mysql_tools.py            # 数据库表查询 / SQL 执行
│   ├── ragflow_tools.py          # RAGFlow 助手列表 / 提问
│   ├── upload_file_read_tool.py  # 文件读取（md/docx/pdf/xlsx）
│   ├── markdown_tools.py         # 生成 Markdown
│   ├── pdf_tools.py              # Markdown 转 PDF
│   └── mysql.sql                 # 建表语句 + 种子数据
├── utils/                        # 工具函数
│   ├── session.py                # 会话工作目录初始化
│   ├── stream.py                 # 流式输出解析
│   ├── path_utils.py             # 路径解析与隔离
│   └── word_converter.py         # Word COM 引擎转 PDF
├── ragflow/                      # RAGFlow 集成 demo 与部署笔记
├── ui/                           # Vue 3 + TypeScript 前端
├── prompt/prompt.yaml            # 主/子智能体提示词
├── output/                       # 生成结果输出
└── updated/                      # 上传文件临时区
```

---

## 三、架构

```
用户 → FastAPI(/api/task + /ws/{thread_id})
           │
           ▼
   run_deep_agent() ── 准备会话环境 → 绑定 ContextVar → 流式执行
           │
           ▼
   main_agent (create_deep_agent)
     ├── 工具：read_file_content / generate_markdown / convert_md_to_pdf
     └── 子智能体：
           ├── 网络搜索助手 ── network_search (Tavily)
           ├── 数据库查询助手 ── list_table_names / show_table_data / execute_sql_data
           └── RAGFlow 助手 ── show_chat_list / create_session_ask
```

---

## 四、功能点

### 4.1 主智能体工具

| 工具 | 功能 |
|------|------|
| `read_file_content` | 读取 Markdown / Word / PDF / Excel 文件内容 |
| `generate_markdown` | 生成 Markdown 文档 |
| `convert_md_to_pdf` | 将 Markdown 转为 PDF（Word COM 引擎，依赖 pywin32） |

### 4.2 子智能体

| 子智能体 | 工具 | 数据来源 |
|----------|------|----------|
| 网络搜索助手 | `network_search` | Tavily 联网检索 |
| 数据库查询助手 | `list_table_names` / `show_table_data` / `execute_sql_data` | MySQL（药品/库存/销售） |
| RAGFlow 知识库助手 | `show_chat_list` / `create_session_ask` | RAGFlow 知识库 |

> 说明：RAGFlow 知识库助手已实现但默认未挂载到主智能体（`main_agent.py` 中已注释），如需启用取消注释即可。

### 4.3 会话隔离与流式输出

- **会话隔离**：`api/context.py` 用 `ContextVar` 隔离并发请求的会话目录与 `thread_id`，避免多用户数据串台。
- **流式输出**：`api/monitor.py` 单例监控器，通过 WebSocket 定向推送工具调用、子智能体委派、任务结果。
- **工作目录**：每个会话在 `output/session_{thread_id}` 创建独立工作目录，上传文件从 `updated/` 迁移至此。

---

## 五、技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI（WebSocket 流式） |
| 智能体 | DeepAgents（create_deep_agent）+ LangGraph checkpointer |
| 大模型 | DeepSeek（`DEEPSEEK_MODEL_NAME` 指定） |
| 数据库 | MySQL（药品 / 库存 / 销售） |
| 知识库 | RAGFlow |
| 联网搜索 | Tavily |
| 文档处理 | Markdown / python-docx / pypdf / pandas / pywin32 |
| 前端 | Vue 3 + TypeScript + Vite + Axios + marked |

---

## 六、环境变量（`.env`）

| 变量名 | 说明 |
|--------|------|
| `DEEPSEEK_API_BASE` / `DEEPSEEK_API_KEY` | DeepSeek 模型鉴权 |
| `DEEPSEEK_MODEL_NAME` | 对话模型名称（如 `deepseek:deepseek-v4-flash`） |
| `DS_MYSQL_HOST` / `DS_MYSQL_PORT` | MySQL 地址 / 端口 |
| `DS_MYSQL_USERNAME` / `DS_MYSQL_PASSWORD` | MySQL 账号 / 密码 |
| `DS_MYSQL_NAME` | MySQL 数据库名（默认 `deep_search`） |
| `TAVILY_API_KEY` | Tavily 联网搜索鉴权 |
| `DS_RAGFLOW_API_URL` / `DS_RAGFLOW_API_KEY` | RAGFlow 服务地址 / 鉴权 |

---

## 七、快速开始

### 7.1 数据初始化（首次运行）

执行 `tools/mysql.sql`，创建 `deep_search` 数据库及 `drugs` / `inventory` / `sales_records` 三张表并写入种子数据。

### 7.2 启动后端

```bash
python -m project.deep_search.api.server
# 或仓库根目录 sh/deep_search_backend.sh
```

后端服务地址：`http://127.0.0.1:8002`

### 7.3 启动前端

```bash
cd project/deep_search/ui
npm install     # 首次运行
npm run dev     # 或仓库根目录 sh/deep_search_frontend.sh
```

---

## 八、API 接口

| 方法 | 路径 | 说明 | 请求体 / 参数 |
|------|------|------|---------------|
| POST | `/api/task` | 启动 Agent 任务 | `{ query, thread_id? }` |
| POST | `/api/upload` | 上传文件到会话目录 | `files` + `thread_id`（表单） |
| GET | `/api/download` | 下载指定文件 | `path`（绝对路径，须在 output 内） |
| GET | `/api/files` | 列出目录下文件 | `path`（绝对路径，须在 output 内） |
| GET | `/outputs/*` | 静态访问生成文件（前端文件卡片使用） | 文件相对路径 |
| WS | `/ws/{thread_id}` | 流式上报任务进度 | — |

---

## 九、注意事项

1. 后端监听 `0.0.0.0:8002`，CORS 已放开，前端可直接跨域调用。
2. `convert_md_to_pdf` 依赖 Word COM 引擎（`pywin32`），仅支持 Windows 环境。
3. RAGFlow 知识库助手默认未启用，需在 `main_agent.py` 中取消注释并配置 `DS_RAGFLOW_API_URL` / `DS_RAGFLOW_API_KEY`。
4. `.env` 含 API Key，请勿提交到版本控制。
