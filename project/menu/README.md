# 智能点餐助手（AiMenu · 一绪寿喜烧）

> 基于 **LangChain Agent + FastAPI + Vue 3** 的餐厅智能助手。
> 后端智能体负责对话、菜品语义检索与餐位预订，前端提供对话交互、FAQ 推荐、菜单与预订信息展示。

---

## 一、项目简介

`project/menu` 是一个完整的餐厅智能助手项目，以「一绪寿喜烧」餐厅为背景，实现了：

- **智能对话**：基于 LangChain Agent，支持多轮对话、流式（SSE）输出。
- **菜品语义检索**：将用户口味描述编码为向量，在 Milvus 中做语义检索，返回匹配菜品。
- **餐位预订**：Agent 收集信息 → 二次确认 → 写入 MySQL 预订单表。
- **FAQ 相似问题推荐**：基于 Redis 存储 FAQ + 向量余弦相似度，输入时实时推荐相关问题。
- **前端展示**：对话、FAQ 推荐、菜单列表、预订信息等一体化界面。

---

## 二、目录结构

```
project/menu/
├── __init__.py
├── .env                          # 独立环境变量（不提交，含 API Key）
├── agent/                        # 智能体后端逻辑
│   ├── langchain.py              # LangChain 餐厅助手 Agent（3 个工具 + 流式对话）
│   ├── milvus_sync.py            # Milvus 向量库初始化脚本（一次性）
│   ├── FAQ/                      # FAQ 数据与 Redis 操作
│   │   ├── redis_demo.py         # Redis 基本操作演示
│   │   └── redis_sync.py         # FAQ 数据导入 Redis
│   └── prompt/                   # 提示词与数据库初始化
│       ├── prompt.py             # 加载系统提示词配置
│       ├── prompt.yaml           # 系统提示词（角色/边界/能力/餐厅信息）
│       └── menu.sql              # MySQL 建表语句 + 种子数据
├── api/                          # FastAPI 服务
│   └── main.py                   # REST 接口 + SSE 流式输出
└── ui/                           # Vue 3 前端
    ├── index.html
    ├── package.json
    ├── package-lock.json
    ├── vite.config.js            # 开发服务器 + /api 代理
    ├── start.bat                 # Windows 一键启动脚本
    ├── dist/                     # 构建产物（npm run build 生成）
    └── src/
        ├── main.js               # 应用入口（注册 Element Plus）
        ├── App.vue               # 根组件（对话 / FAQ 推荐 / 菜单 / 预订）
        └── api/
            └── index.js          # API 服务层（axios + fetch 流式）
```

---

## 三、功能点

### 3.1 智能对话（`POST /chat`）

- 基于 LangChain `create_agent`，使用 **DeepSeek（deepseek-v4-pro）** 模型，关闭 thinking 模式。
- 通过 `InMemorySaver` checkpointer 按 `thread_id` 隔离并保留多轮对话历史。
- 后端以 SSE（`text/event-stream`）流式返回，前端逐 token 渲染，并支持简单的 Markdown 加粗/换行。

### 3.2 Agent 工具（`agent/langchain.py`）

| 工具 | 功能 | 数据来源 |
|------|------|----------|
| `search_main_dishes` | 查询特色主菜（`is_featured=1`），字段映射为中文标签 | MySQL |
| `user_flavor_search` | 按用户口味语义检索菜品，返回最接近的菜品 | Milvus 向量库 |
| `make_reservation` | 餐位预订，写入 `reservation_order` 表（参数化查询防注入） | MySQL |

系统提示词在 `agent/prompt/prompt.yaml` 中定义角色、工作边界、预订流程与餐厅信息，保证 Agent 不越界、不捏造。

### 3.3 FAQ 相似问题推荐（`POST /faq/suggest`）

- FAQ 数据存储于 Redis：`faq:all_items` 集合保存所有 key，每个 key（`faq:items:*`）为 `{question, answer}` 哈希。
- 后端将所有 FAQ 问题与用户输入编码为 embedding 向量，计算**余弦相似度**，返回相似度最高的 **top-k（默认 2）** 个问题及答案。
- 前端交互：用户输入提问时，**停止输入 500ms** 后自动调用该接口，在输入框上方展示推荐问题；点击问题即返回对应答案。

### 3.4 前端界面（`ui/src/App.vue`）

| 区域 | 功能 |
|------|------|
| 智能对话 | 聊天历史、流式输出、欢迎语快捷提问 |
| FAQ 推荐 | 输入防抖 500ms 触发，展示推荐问题，点击返回答案 |
| 菜单列表 | 展示分类 / 辣度 / 素食标签，Agent 推荐菜品时高亮 + 闪烁 |
| 预订信息 | 展示最近预约记录，支持刷新 |

> 说明：代码中保留了购物车加购 / 下单的相关逻辑，但当前界面未渲染对应区域。

---

## 四、技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI |
| 智能体 | LangChain + LangGraph（checkpointer） |
| 大模型 | DeepSeek（deepseek-v4-pro） |
| 嵌入模型 | OpenAI 兼容接口的向量嵌入模型 |
| 数据库 | MySQL（菜单 + 预订单） |
| 向量库 | Milvus（菜品语义检索，HNSW + 余弦相似度） |
| 缓存 | Redis（FAQ 相似推荐） |
| 前端 | Vue 3 + Vite + Element Plus + Axios |

---

## 五、环境变量（`.env`）

| 变量名 | 说明 |
|--------|------|
| `MYSQL_HOST` / `MYSQL_PORT` | MySQL 地址 / 端口 |
| `MYSQL_USERNAME` / `MYSQL_PASSWORD` | MySQL 账号 / 密码 |
| `MYSQL_NAME` | MySQL 数据库名（默认 `menu`） |
| `MENU_MILVUS_URL` | Milvus 服务地址 |
| `MENU_REDIS_URL` | Redis 服务地址 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | 嵌入模型鉴权（OpenAI 兼容接口） |
| `MENU_EMBEDDING_MODEL` | 嵌入模型名称 |

> 参考项目根目录的 `.env.example` 配置模板。

---

## 六、快速开始

### 6.1 数据初始化（首次运行）

1. **初始化 MySQL**：执行 `agent/prompt/menu.sql`，创建 `menu_items`（菜单）与 `reservation_order`（预订单）两张表并写入种子数据。

2. **初始化 Milvus 向量库**：将菜品文本编码为向量写入 `menu` 数据库的 `menu_items` 集合。

   ```bash
   python -m project.menu.agent.milvus_sync
   ```

3. **导入 FAQ 到 Redis**：将 FAQ 问答写入 `faq:all_items` / `faq:items:*`。

   ```bash
   python -m project.menu.agent.FAQ.redis_sync
   ```

### 6.2 启动后端

```bash
python -m project.menu.api.main
# 或 uvicorn project.menu.api.main:app --host 127.0.0.1 --port 8000
```

后端服务地址：`http://127.0.0.1:8000`

### 6.3 启动前端

```bash
cd project/menu/ui
npm install     # 首次运行
npm run dev     # 或直接双击 start.bat
```

前端地址：`http://localhost:3000`

---

## 七、API 接口

| 方法 | 路径 | 说明 | 请求体 / 参数 |
|------|------|------|---------------|
| POST | `/chat` | 智能对话（SSE 流式输出） | `{ query, thread_id }` |
| POST | `/faq/suggest` | FAQ 相似问题推荐 | `{ query, thread_id }` |
| GET | `/health` | 健康检查（含数据库连通性） | — |
| GET | `/menu/list` | 可售菜品列表 | — |
| GET | `/reservation/list` | 最近预约记录 | `limit`（1–100，默认 10） |

### 请求 / 响应示例

**`POST /faq/suggest`**

请求体：

```json
{ "query": "你们几点营业", "thread_id": "xxx" }
```

响应：

```json
{
  "success": true,
  "query": "你们几点营业",
  "suggestions": [
    { "question": "营业时间", "answer": "周一至周五 12:00-21:00..." },
    { "question": "餐厅电话", "answer": "010-66666666..." }
  ]
}
```

---

## 八、前端代理配置

开发环境下，`vite.config.js` 将 `/api` 路径代理到 `http://127.0.0.1:8000`，并移除 `/api` 前缀。后端接口路径与前端调用保持一致，无需跨域配置。

---

## 九、注意事项

1. 确保后端服务运行在 `http://127.0.0.1:8000`，前端代理默认指向该地址。
2. `agent/langchain.py` 依赖 `milvus_sync.py` 先完成向量库初始化，否则口味检索工具会查询失败。
3. `.env` 含 API Key，请勿提交到版本控制。
4. 若需修改后端地址，请同步调整 `vite.config.js` 中的代理配置。
