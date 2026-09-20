# CharApp 落地计划

> **一句话**：用 `CharAgent` 通用 agent 框架，为 `app/minimall` 商城装配一个多用户电商智能客服助手；业务侧走完企业生产级全链路，框架侧在此过程中被真实压力验证。

## 0. 定位与目标

| 目标 | 内容 |
|------|------|
| 主 | 通用 agent 框架 `CharAgent` 的组件搭建与对接（从 0 手写，对业务零知识） |
| 次 | 业务侧 `CharApp` 作为验证载体，走完生产级全链路 |
| 产出 | 求职作品 —— 可演示、可讲述架构决策 |

**硬约束（可断言的）**：
- 依赖方向严格单向 `CharApp → CharAgent`，框架不 import 任何 Django / minimall / CharApp 代码
- 框架可被第二个业务复用（由一条冒烟测试证明，见 §6.1）

## 1. 架构总览

### 终态（L1b 之后）

```
┌──────────────────┐   session cookie    ┌─────────────────────────┐
│ 浏览器            │ ──────────────────> │ Django :8000            │
│ minimall 客服页面 │ <────────────────── │  BFF 端点 + 业务内部端点 │──> MySQL
└──────────────────┘   SSE (透传)        └─────────────────────────┘
                                                    ↑ X-Internal-Token
                                                    │ X-User-Id
                                         ┌──────────┴──────────┐
                                         │ CharApp 服务 :1007   │
                                         │ (FastAPI + SSE)     │
                                         └──────────┬──────────┘
                                                    │ 进程内调用
                                         ┌──────────┴──────────┐
                                         │ CharAgent 框架(纯库) │
                                         └─────────────────────┘
```

### 信任模型

| 边界 | 认证方式 |
|------|---------|
| 浏览器 ↔ Django | **session cookie（唯一的真实用户认证点）** |
| Django ↔ CharApp | `X-Internal-Token` + `X-User-Id` |
| CharApp ↔ minimall 内部端点 | `X-Internal-Token` + `X-User-Id` |

### 身份贯穿路径

```
Django session → user_id → X-User-Id → RunContext.payload["user_id"] → 工具内部取用
```

**关键性质**：`user_id` 永不进入工具的函数签名，因此永不出现在暴露给模型的 wire schema 里 —— 模型既看不见也改不了它，"诱导模型查他人订单"这条攻击路径天然不存在。

## 2. 阶段划分

| 阶段 | 内容 | 验收标准 |
|------|------|---------|
| **L1a** | 框架 `ToolProvider` 接缝；minimall 只读内部端点；CharApp 9 个只读工具 + CLI | CLI 问"推荐个手机"/"我的订单到哪了" → 命中真实数据 |
| **L1b** | `CharAgent/server/`（SSE + cancel）；Django BFF；minimall 客服页面 | 浏览器打开客服页，多用户各自登录、各自看到自己的数据 |
| **L2** | hook 拦截点（`before_tool_execute` + 返回值语义）；`#69` prompt 版本化；minimall 写操作端点 + `RefundRequest` 扩建；轨迹断言测试 | 挂起/拦截行为可被测试断言；下单/退款链路跑通 |
| **L3** | 可观测（trace 落 PG + 成本记账 + 脱敏）；HITL 框架级挂起（替代 prompt 层确认） | 能回答"当时它看到了什么、花了多少" |
| **L4** | 评估集 + 自动跑分；prompt A/B；**工具数量 A/B（全挂 vs 动态裁剪）** | 能证明"这次改得比上次好"，且两个 A/B 有对照数据 |

**L1a 不可跳过**：Web 会同时引入 HTTP 层、SSE、BFF 转发、前端渲染、跨进程错误传播五个新变量。链路本身未验证时不加传输层。

## 3. L1a 详细设计

### 3.1 框架侧（`CharAgent/`）

| # | 改动 | 说明 |
|---|------|------|
| 1 | 新增 `agent/provider.py` | `RunContext`（`thread_id` + 不透明 `payload`）+ `ToolProvider` Protocol（`async def provide(ctx) -> Sequence[Tool]`）。**落在 `agent` 包**而不是 `tool` 包：它是运行时装配层，与 `AgentLoop` 同层 —— `tool` 包管「工具是什么」，不管「这次拿哪些」 |
| 2 | `agent/__init__.py` + 根 `__init__.py` | 上浮 `RunContext` / `ToolProvider`。`tests/test_root_facade.py` 的防漂移用例自动覆盖，**不需要改它** |
| 3 | `client/session.py` 加 `prompt_name` / `prompt_dir` 参数 | `tools` **已是构造参数，无需改动**；只需把硬编码的 `load_prompt("system", ...)`（`session.py:148`）一处参数化，默认值保持现状使 CLI 行为不变 |
| 4 | `prompt/load.py` 加 `prompt_dir` 参数 | 默认框架目录，业务侧可指向自己的 prompt 目录（keyword-only，写进 `**values` 之前，不会被当成模板变量） |
| 5 | 新增 `pyproject.toml` | 使 `pip install` 成立，依赖 8 个：`httpx` `openai` `pydantic` `python-dotenv` `sqlalchemy` `psycopg` `redis` `alembic`。**`namespaces` 必须显式设 false**：默认那档会把 `tests/` `docs/` `alembic/` 等待十个非包目录一起收进 wheel |

**核心设计约束**：`agent/loop.py` **零改动**（已核实：`git diff --name-only CharAgent/agent/loop.py` 为空）。若加一个业务接入点需要改循环核心，说明接缝设计失败。

**实现时定下的两处**（与上表初稿有出入，以代码为准）：

1. **`ToolProvider` 不由 `ChatSession` 解析**。业务的装配代码自己 `await provider.provide(context)`，再把工具交给 `ChatSession` —— `ChatSession` 至今不知道 `RunContext` 存在。这样框架不必「为了调用而持有业务数据」（payload 里是什么，框架从装配到运行都不经手），也避免了 `__init__` 是同步的而 `provide` 是异步的这一冲突。代价是业务入口多一行 `await`。
2. `provide` 返回 `Sequence[Tool]` 而非 `list[Tool]` —— `list` / `tuple` 都能交，框架不挑返回的是什么容器。

### 3.2 minimall 侧（`app/minimall/`）

**新增 `views_agent.py` + `urls_agent.py`，前缀 `/api/minimall/agent/`，`app_name = "minimall_agent"`。**

| # | 端点 | 认证 | 返回 |
|---|------|------|------|
| 1 | `GET products/` | token | 商品列表（支持 search/category/min_price/max_price/ordering/page/page_size） |
| 2 | `GET products/<slug>/` | token | 商品详情（**含 stock**） |
| 3 | `GET categories/` | token | 分类树 |
| 4 | `GET featured-products/` | token | 精选商品（`is_featured`） |
| 5 | `GET cart/` | token + user | 购物车 |
| 6 | `GET orders/` | token + user | 订单列表（支持 page/page_size） |
| 7 | `GET orders/<order_no>/` | token + user | 订单详情 |
| 8 | `GET profile/` | token + user | 余额与基本信息 |
| 9 | `GET addresses/` | token + user | 地址列表 |

**两条必须落实的规则**：

- **`X-Internal-Token` fail closed**：未配置 `CHARAPP_INTERNAL_TOKEN` 环境变量时拒绝所有请求（复用 `charplot` 已建立的模式）。用 `secrets.compare_digest` 比较。
- **全部直查 DB，不经 Redis 缓存**。理由：`products/<slug>/` 的缓存 TTL 为 600s ±20%，agent 若据此回答"还剩 3 件"可能是 12 分钟前的数据，而 agent 的回答会被用户当作事实。新增 `serializers_agent.py` 显式声明这是**面向 agent 的契约**（含 `stock`），而非用户面接口的镜像。

**不动的部分**：L1a **一个模型都不改**。`RefundRequest`、`ship_order` 端点、`pay_order` 的加锁修复全部推后到 L2（归因性：一次只动一层）。

### 3.3 CharApp 侧（`CharApp/minimall/`）

```
CharApp/
├── CONTEXT.md                    # 已建：领域词汇
├── docs/
│   ├── PLAN.md                   # 本文件
│   └── adr/0001-internal-endpoint-trusts-declared-user-id.md
├── minimall/
│   ├── client.py                 # httpx.AsyncClient → minimall 内部端点
│   ├── provider.py               # MinimallToolProvider(ToolProvider)
│   ├── tools.py                  # 9 个 async 工具
│   ├── prompt/system/v1.prompt   # 写实客服的 system prompt（按 {名字}/{版本} 落盘）
│   └── cli.py                    # L1a 薄入口
└── tests/
```

**9 个只读工具**（全部 `async def` + `httpx.AsyncClient`）：

| 域 | 工具 | 说明 |
|----|------|------|
| 商品 | `search_products(keyword?, category?, min_price?, max_price?, ordering?, page?, page_size?)` | 关键词搜索 + 筛选 + 排序 + 分页 |
| 商品 | `get_product_detail(slug)` | 详情含库存 |
| 商品 | `list_categories()` | 分类树 |
| 商品 | `list_featured_products()` | 精选商品 |
| 购物车 | `get_my_cart()` | 只读购物车 |
| 订单 | `list_my_orders(page?, page_size?)` | 订单列表 |
| 订单 | `get_my_order(order_no)` | 订单详情 |
| 账户 | `get_my_profile()` | 余额 |
| 账户 | `list_my_addresses()` | 地址列表 |

**命名约定**：**动词开头 + 名字里含 `my`**（`get_my_cart` / `list_my_orders` / `get_my_order` /
`get_my_profile` / `list_my_addresses`）—— 动词开头与框架 `tools_demo` 的命名一致；`my` 是给模型的
语言提示（"这个工具查的是当前对话者自己的东西"），也强化"身份不可指定"。

**分页约定**：`page` 从 1 开始；`page_size` 是**闭集** `5 / 10 / 20 / 50 / 100`（与商城买家侧白名单
`views_agent.PAGE_SIZE_OPTIONS` 同源，约束写进 schema 而不是说明里 —— 模型连填错的空间都没有），
工具级默认 100（商城侧不传时默认 20；端点对白名单外的值是**回退默认**而不是截断，
`_paginate` 一处兜底）。返回体里带 `count` 与 `total_pages`。

**实现约束（`#65` 真实案例）**：框架 `execute_tool` 把同步工具函数扔进 `asyncio.to_thread`，因此业务工具必须写成 `async def` + `httpx.AsyncClient`，否则 9 个工具会占满线程池。

### 3.4 L1a 验收

| 层 | 验收项 |
|----|--------|
| 框架 | `pytest` 756 用例全绿；新增 ToolProvider 契约测试 + 第二业务冒烟测试（§6.1） |
| minimall | 新增 `tests/test_agent_api.py`：无 token → 403；错 token → 403；无 `CHARAPP_INTERNAL_TOKEN` → 全拒；用户隔离（A 的 id 取不到 B 的数据）；改库存后立即反映（证明未走缓存） |
| CharApp | 工具单测（respx mock HTTP）；`MinimallToolProvider` 契约（9 个工具、**schema 里不含 `user_id`**）；CLI 端到端冒烟（mock LLM 驱动一次查商品） |
| 人工 | CLI 跑通两条真实问答 |

### 3.5 工程侧

- 根 `.env.example` 加 `CHARAPP_INTERNAL_TOKEN` 与 `CHARAPP_*` 段
- `sh/charapp_client.sh` 启动脚本（沿用项目 `sh/` 惯例）
- 更新根 `CLAUDE.md`（新增 `CharAgent/` `CharApp/` 两类顶层目录的约定）与 `README.md`

## 4. L1b 概要

1. `CharAgent/server/`：FastAPI，SSE 推送（消费 `stream/` 事件总线）+ `POST runs/{id}/cancel` + 会话接口
2. `app/minimall/views_bff.py`：`/api/minimall/agent/chat/`（session 认证 → 取 `user_id` → 转发 CharApp → SSE 透传回浏览器）
3. `templates/minimall/` 客服页面 + 商品页入口链接（原生 JS + `EventSource`）
4. `thread_id = f"minimall:{user_id}:{conversation_id}"` —— 框架 checkpoint 按此分区，多用户会话天然隔离

## 5. L2–L4 概要

**L2**：hook 拦截点（`before_tool_execute` + 返回值语义，配轨迹断言）· `#69` prompt 版本化收尾（目录布局已就位：`prompt/system/v1.prompt`；还差**清单文件声明当前默认用哪一版**与 **trace 记录实际命中的版本**）· minimall 写操作端点（加购/下单/支付/取消）· `RefundRequest` 扩建（仅退款、全额、三态、Admin 审批、补 `refunded_at`）· 顺带修 `pay_order` 的缺失事务与行锁

**L3**：`CharAgent/plugins/observability`（trace 落 PG，run 粒度一行 + JSONB 明细）· 成本记账（`on_model_call` AFTER 的 usage）· 脱敏 · HITL 框架级挂起（`PendingApproval` → checkpoint 挂起 → `resume()`），替代 L1 的 prompt 层确认

**L4**：评估集（golden set 起步 + badcase 回流）· prompt A/B · **工具数量 A/B**：全挂 9→17 个 vs `before_turn` 动态裁剪，用准确率差值决定 `#70` 的取舍

## 6. 风险与开放项

### 6.1 「通用性」的验证方式（Q5 结论）

`CharAgent/tests/` 里加一条冒烟测试：用**同一套** `AgentLoop` 装配两个不同的 `ToolProvider`（电商的 + 一个与电商无关的极小实现），断言框架代码里不存在业务词的 import。这把"通用"从形容词变成可执行断言。

### 6.2 已核实的实现细节（2026-09-18 读码确认；行号 2026-09-19 复核）

**结论：`ChatSession` 无 CLI 耦合，可直接上浮为公共 API。** 证据逐项：

| 潜在耦合点 | 实际状况 |
|-----------|---------|
| `tools` 是否硬编码 | **已是构造参数**（`session.py:114`）。`DEMO_TOOLS` 只是 `client/__init__.py` 导出的模块常量，由 `app.py` 传进来 |
| 事件渲染 | `event_sink` 注入的是 `EventSink` 协议（`session.py:117`），不依赖 `render.EventPrinter` |
| `KillSwitch`（上浮前叫 `_KillSwitch`） | 完全在 `app.py:281`；`session.ask()` 的 docstring 明说 CancelledError「由 app.py 接住」 |
| 交互命令（`/resume` 等） | 在 `client/utils/commands.py`，与 session 无关 |
| 唯一 CLI 特有 import | `DEFAULT_THREAD_ID`（一个字符串常量，无害） |
| **真正需要改的** | 只有 `session.py:148` 的 `load_prompt("system", ...)` 一处 —— 把 prompt 名与目录参数化 |

**`build_model` 不上浮**：签名是 `build_model(options: CliOptions, writer) -> ChatModel`（`app.py:223`），依赖 CLI 专属的 `CliOptions` 与终端输出回调，是 CLI 的活。业务侧自己写三行装配（`RetryingChatModel(chat_model_from_env(...))`）即可 —— `chat_model_from_env` 已是 `model/` 包的公共 API。

**额外收获：HITL 的底层比预估更就绪。** `session.resume()` 的 docstring（`session.py:242-244`）明确写着「快照恰好停在『工具调用还没有结果』的半路时（**人工审批挂起点**），loop 还会把那几条欠着的调用补做完再继续（`checkpoint/utils/pending.py`）」—— 即 `#25` 审批的**恢复路径已经通了**。L3 只需补「拦截 + 挂起」那一半，不需要动 checkpoint。

仍需动手时确认的一项：

| 项 | 说明 |
|----|------|
| `charplot` 的 `X-Internal-Token` 实现 | 需读 `app/charplot/views_api.py` 取现成模式，避免两套写法 |

### 6.3 已记录的一条 ADR（2026-09-19 完成）

`CharApp/docs/adr/0001-internal-endpoint-trusts-declared-user-id.md` —— 内部端点信任 `X-User-Id`。三条 ADR 条件均满足：改认证契约要动所有内部端点（难回退）· 面试官必问"为什么信任裸 user_id"（无上下文时反直觉）· 存在真实取舍（单机演示 vs JWT 签发/验签/时钟偏移/密钥轮换）。已记入 ADR：**身份来路**（session cookie 是唯一验证点，裸声明只是同一信任域内的转发）· **什么时候必须还**（判据是信任域被拆开，不是用户数）· **生产化路径 = 内部 JWT（`sub` claim）或 mTLS + 服务网格身份**（换的只是传输，验证点不变）· 代价说明 · 三个被否的替代方案（身份做成工具参数 / 现在就上 JWT / 每个端点各自校验）。

### 6.4 文档基线（已定）

**以当前磁盘状态为初始基线，不恢复任何已删文档。** `CharAgent/docs/adr/`、`docs/CONTEXT.md`、`docs/design/`、CharService 规划文档**永久废弃**，不再重建；`.scratch/` 保持为空。

**`CharAgent/docs/DESIGN.md` 是唯一例外，保留。** 理由：它在磁盘上（属于"当前状态"，不是需要"恢复"的东西），且是 `difficulties/` 14 份分册（已提交）的**唯一索引** —— 删掉它会让分册失去入口。它与 `difficulties/` 一起构成框架的「难点地图」，定位是参考资料而非规划文档，与本项目后续的 `CharApp/docs/PLAN.md` 互不覆盖。已于 `be2d17b` 纳入版本控制。
