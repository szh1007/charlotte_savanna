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
| **L1a** ✅ | 框架 `ToolProvider` 接缝；minimall 只读内部端点；CharApp 9 个只读工具 + CLI | CLI 问"推荐个手机"/"我的订单到哪了" → 命中真实数据（2026-09-19 完成，issues 01–03） |
| **L1b** ✅ | `CharAgent/server/`（SSE + cancel）；Django BFF；minimall 客服页面 | 浏览器打开客服页，多用户各自登录、各自看到自己的数据（2026-09-21 完成并人工验收，issues 04–07） |
| **L2** ✅ | hook 拦截点（`before_tool_execute` + 返回值语义）；`#69` prompt 版本化（清单文件）；minimall 写操作端点 + 退款域（**新建** `RefundRequest`）；17 个工具 + 护栏插件；工具事件去字段化 + 会话历史；顺带修三处缺事务/锁的写路径 | 拦截行为可被测试断言；**下单与退款链路跑通**（浏览器下单 → **买家在订单页付款** → 申请退款 → 管理员批准 70 元 → 打款 → 助手答得出退了 70）。2026-09-22 真机跑通（issues 08–14），**同时跑出三条欠账与一次领域规则改判，收口见 issue 15** |
| **L2.5** ✅ | **会话治理**（原计划外，用户要求的中间阶段）：框架侧上下文压缩（账本/视图分离 + 滚动摘要 + 工具结果截断）· 框架侧会话记录与水合（记录落库 + 重启后拿得回历史 + 会话列表端点）· 业务侧接入（快照换 Postgres）· 前端左侧会话列表 · 会话管理动作（重命名 / 删除 / 搜索 / 置顶） | 真机：聊几句 → **重启服务** → 前端历史还在且模型接着上文答；长对话触发压缩后单请求 token 不再增长、而用户看到的历史一条不少；左侧列表能新建与切回。**2026-09-23 真机跑通**（issues 15–21），核心两条在 issue 18 验收、前端两条在 19/20 验收，收口见 issue 21 |
| **L3a** ✅ | 工具调用轨迹落库（`charagent_tool_calls` 的**第一个生产调用方**）；成本口径 + `trace` 只读入口；日志脱敏；两条搭车欠账（BFF 共享 client / 历史响应闸门） | CLI 能列出**每一次工具调用**（工具名 / 参数 / 结果 / 耗时 / 状态）并报出这次运行花了多少；日志脱敏可被断言（构造一条含手机号的日志，落盘后搜不到原文）。**2026-09-25 真机跑通**（issues 27–31）：浏览器提问 → `trace` 打出每次调用与金额 ¥0.001283；脱敏那条由测试落真文件验过 |
| **L3b** ✅ | 幂等持久化（#17 的**第一个真实调用方**）；`resume()` 记账；**HITL 框架级挂起**（`Decision` 第三值 + `approval_required` 事件 + `POST /runs/{id}/resume`）；**助手代付**（本人在页面输密码）；**下单前确认** | 真机：说「帮我付了这单」→ 页面**弹确认卡** → 输密码 → 付款成功。三条否定断言同时成立：会话历史搜不到密码原文 · `charagent_tool_calls.arguments` 里没有密码 · `resume` 重放两次**只扣一次钱**。**2026-09-26 真机跑通**（issues 32–38）：两条触发面各跑一遍（「下单吧」→ 只有两颗按钮的卡 → 确认 → 订单下成；「帮我把这单付了」→ 密码卡本人输 → 订单变 `paid`、余额 19505.00 → 19406.00），五条否定断言逐条有结论（见 §5 的 L3b 段） |
| **L4** | 评估集 + 自动跑分；prompt A/B；**工具数量 A/B（17 全挂 vs 动态裁剪）**；prompt 里要求模型不复述工具真实数据；「17」去数字化（35 处 / 10 文件）+ 根 `CLAUDE.md` / `README.md` 同步 | 能证明"这次改得比上次好"，且两个 A/B 有对照数据 |
| **L5** | **RAG + 注入防护**（DESIGN ⑧ #45–49 + #23）：知识库（商品与政策文档）→ 摄取清洗 → 切分 → 混合检索 + rerank → 引用溯源；**随 RAG 一起把 #23 的三个条件补齐** | 助手答得出政策类问题且**逐句可溯源**；构造一条投毒文档，注入被拦下 |

> **L2 的详细规划已就位**（2026-09-21）：`../.scratch/CharApp/issues/08`–`14`，共 7 片。
> 关键路径 `09 → 10 → 11 → 12`；`08` 与 `09` 可并行、`13` 与 `11` 可并行。
> 决策记录见 `docs/adr/0003`、`0004`，需求变更见 `../.scratch/CharApp/PRD.md` 的「修订记录」。
>
> **L2.5 的详细规划已就位**（2026-09-22）：`../.scratch/CharApp/issues/15`–`21`，共 7 片。
> 关键路径 `17 → 18 → 19 → 20`；`15`（L2 验收收口）与 `16`（上下文压缩）可与 `17` 并行。
> 它**不在任何原计划里**，是用户要求的中间阶段 —— 理由、边界与验收见 §5。
> **实际做完 12 片**（2026-09-24 补记）：`21` 收口后又冒出 `22`–`26`（帧与运行双向溯源 ·
> 压缩视图隔轮失效 · 四条迁移压成一条 · 会话面板手感 · 压缩正确性与降级收口），全部完工。
>
> **L3a / L3b 的详细规划已就位**（2026-09-24）：`../.scratch/CharApp/issues/27`–`38`，共 12 片。
> 关键路径 `27 → 33 → 34 → 35 → 36 → 37`；`29` / `30` / `32` 与主线无依赖。**L5 只定了阶段位置，尚未切 ticket。**
> **`27` 是整条 L3 的地基** —— L3a 的观测与 L3b 的挂起**都**落在它上面。
> 与 2026-09-19 那版 L3 的三处出入：**① L3 拆成 L3a / L3b**（可观测与 HITL 是两块独立
> 可验收的东西，混一片会让"可观测做完没有"无法判定）· **② `#15` 工具超时从 L3b 摘掉**
> （改判理由见 `adr/0017`）· **③ 新增 L5（RAG + 注入防护）** —— 它在原计划里**零排期**，
> 而用户的既定目标里点名了它（见 §6.5）。

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

- 根 `.env.example` 加 `CHARAPP_INTERNAL_TOKEN` 与 `CHARAPP_*` 段 ✅
- `sh/charapp_client.sh`、`sh/charapp_backend.sh` 启动脚本（沿用项目 `sh/` 惯例）✅
- ~~更新根 `CLAUDE.md`（新增 `CharAgent/` `CharApp/` 两类顶层目录的约定）与 `README.md`~~
  → **已决意压后**：2026-09-18 的指示是「两个模块完全实现之前不要动根文档」，2026-09-21 复核为
  「**整个项目正式完成后**才同步」。**这不是漏项** —— 中途同步要写大量马上会变的中间态描述。
  收口时由 issue 14 之外的独立动作完成（届时本行改回 ✅）。

## 4. L1b 概要

1. `CharAgent/server/`：FastAPI，SSE 推送（消费 `stream/` 事件总线）+ `POST runs/{id}/cancel` + 会话接口
2. `app/minimall/views_bff.py`：`/api/minimall/agent/chat/`（session 认证 → 取 `user_id` → 转发 CharApp → SSE 透传回浏览器）
3. `templates/minimall/` 客服页面 + 商品页入口链接（原生 JS + `fetch` 读 SSE 流；原计划写的是 `EventSource`，2026-09-21 改掉，理由见 `adr/0002`）
4. `thread_id = f"minimall:{user_id}:{conversation_id}"` —— 框架 checkpoint 按此分区，多用户会话天然隔离

## 5. L2–L4 概要（含后加的 L2.5）

**L2**（**已详细规划为 issues 08–14**，2026-09-21）：hook 拦截点（`before_tool_execute` + `Tool.annotations` + `HookRegistry.decide()`，配轨迹断言）· `#69` prompt 版本化收尾（清单文件 `prompt/manifest.yaml` 声明默认版本；**记录实际命中的版本归 L3**，它要落进轨迹）· minimall 写操作端点 8 个（加购/改量/移除/清空/下单/取消/退款申请/退款列表）· **`RefundRequest` 新建**（不是"扩建" —— 它此前根本不存在）· 退款域整片：三态 + `refunding` 订单状态 + `refunded_at` + `RefundRequestAdmin`，**含管理员协商金额的部分退款** · 17 个工具（9 只读 + 8 写）· 业务侧护栏插件（写操作预算 8 次 + 单笔金额上限 5000）· 工具事件去字段化（ADR-0003）· 会话历史接口 · 顺带修商城三处缺事务/锁的写路径

**L2.5**（**已详细规划为 issues 15–21**，2026-09-22）：**会话治理**。它**不在任何原计划里**，是用户要求的中间阶段，也不是 L3 / L4 的一部分。起因是两条真问题：① **一个会话聊久了，快照里的历史一直长** —— 而 `conversation_id` 在标签页里是固定的，于是每一轮都要把全量历史重发一遍，成本与首字延迟随轮数线性上涨；框架难点清单里对口的是 **#7 上下文压缩（P1，「摘要 + 截断，但绝不能破坏 `tool_calls` 的结构」）**，那条至今没实现。② **历史只在进程内存里** —— 前端刷新能恢复（issue 13 已验收），但**服务一重启就全没了**，而且不只是前端看不到：`ChatSession` 构造时 `_history` 只有 system 提示、server 路径从不调 `resume()`，所以**模型也看不到上一进程聊过什么**。换成 Postgres 后端**不会**自动解决这件事（`/history` 读的从来不是 saver）。

两件事的落点：**① 上下文压缩** —— 账本/视图分离（快照 append-only 存全量，发给模型的是「系统提示 + 压缩摘要 + 最近 N 轮」的视图；用户看到的记录永不压缩），业务侧配参数（阈值 / 保留轮数 / 工具结果截断 / 摘要开关），新增第七类流事件让压缩看得见。**② 会话记录与水合** —— 把「给人看的记录」落进框架 `db/` 层（`charagent_threads` / `charagent_messages` 两张表与仓储早就写好了，但**从来没有一个生产调用方** —— 这正是 PRD §1 批评的「预设但没人用过的接口」），补一条只读水合让重启后模型与前端都拿得回历史，再加会话列表端点、前端左侧列表（新建 / 切换）与会话管理动作（重命名 / 删除 / 搜索 / 置顶）。**片 15 是 L2 的验收收口**：真机跑出的三条欠账（Admin 两段式动作在真实 UI 不可用、被业务拒绝的操作被显示成「已完成」、验收链缺「买家付款」那一步）+ 一次领域规则改判（取消 / 退款边界与库存回滚判据，见 `../.scratch/CharApp/PRD.md` 的修订记录）。

**L3a（可观测，已详细规划为 issues 27–31）**：先说**原定三件事里有两件半已经被 L2.5 提前兑现**，别再当欠账做一遍 ——

| 2026-09-19 那版 L3 的条目 | 实际状态（2026-09-24 核实） |
|---|---|
| 把 prompt 实际命中的版本记进轨迹 | **已做**：ADR-0005 已填 `charagent_runs.prompt_version`。**只到名字**（`prompt/ref.py` 渲染后的 sha256 不落库），逐轮版本也没有 —— 但帧 v7 的 `metadata.view.prompt_ref` 是完整的，够用 |
| 成本记账 | **数据源已做**：ADR-0005 已建 5 个用量列；`on_model_call` 的 **AFTER 相载荷里已经带 `usage` 与 `elapsed_ms`**，挂载点是现成的 |
| 「当时它看到了什么」 | **数据源已做**：帧的 `metadata.view`（issue 22）+ `estimate_drift` / `cache_hit_ratio`（issue 26） |

真正空的是三件：**① 工具级轨迹**（`charagent_tool_calls` 表、`ToolCall` 实体、`ToolCallsRepository`、连 `needs_approval` + `approved_by` 两列**全都建好了** —— 全仓**零生产调用方**，`db/recorder.py` 只写 threads / runs / messages 三张表；**2026-09-25 补注：这一句已是过去时** —— issue 27 落了它的第一个生产调用方，`ADR-0009` 里那句自陈也在同一天划掉）· **② 成本口径与 `trace` 只读入口** · **③ 日志脱敏**。

**落点的判据（issue 27/28 核实后定的）**：**事实与控制状态走 `ConversationRecorder`**（工具调用行、状态、耗时、结果），**金额在查询侧派生**（不落库；**后半句已于 2026-09-25 改判为「收尾算好写死」，见 ADR-0018 与下面那段真机结论**）。判据是 `hooks` 包自己写的那条 —— `decide` 类的点管控制，`fire` 类的点管派生；而 **HITL 的挂起态不能放插件**：它必须在挂起**之前**落库，而 `fire` 类钩子的异常只记一笔、工具照跑，兜不住。所以 **`27` 是 L3b 的地基**。

> **核实后的修正（2026-09-24）**：L3a **不需要新建任何插件** —— 早先提到的 `CharAgent/plugins/observability` 不必存在。工具轨迹落记录层（控制状态的宿主）、成本分量已经由 ADR-0005 落进 `runs` 五列（**折算口径已于 2026-09-25 改判为「收尾那一刻算好写死」，见 ADR-0018**）、日志脱敏是一个被调用的 `Redactor` 协议而不是挂载点。**第一个框架侧的真实 hook 注册方出现在 L3b**（业务注册的那条"需确认"裁决走 `BEFORE_TOOL_EXECUTE`）。这条要如实写进 L3a 的收口记录 —— "本阶段没有新增插件"是核实后的结论，不是漏项。

> **L3a 的真机结论（2026-09-25，issues 27–31 收口）**：四片全部落地，上面那两条验收都逐条跑过。
>
> - **① 工具调用轨迹**（27）：`charagent_tool_calls` 有了第一个生产调用方。真机（浏览器 → BFF → 服务 → 模型）一次提问两次调用各一行，`tool_name` / `arguments` / `result` / `duration_ms` / `status` 齐全；带参数的调用（`{"order_no": "…"}`）与「这次没调工具」的空态都验过。
> - **② 成本与 `trace`**（28）：`trace <run_id>` 一次打出工具清单 + 三档用量 + 金额与算式。**口径改判**：金额不是「查询侧派生」，是**运行收尾那一刻按当时那一版价目表算好写死**（ADR-0018）—— 供应商会调价，而账单是按当时那版开的；`total_cost` 随之从「保持 0」改成**可空 + 明细列**。真机 ¥0.001283（11552 in / 161 out，谷价），算式逐档可验算。
> - **③ 日志脱敏**（29）：框架给规则与 `Redactor` 协议、业务给字段名单，写之前打码。**边界**：框架自己打的异常栈还没过这个出口（框架目前没有统一的日志出口），落点记在 `DESIGN.md` 的 #38。
> - **④ 两条搭车**（30）：BFF 共享 client（690~970 ms → **十几毫秒**：issue 30 当天 2~16 ms，收口当天拿整套服务重跑 15.1~17.3 ms 中位数；取舍记 **ADR-0020**）· 历史响应闸门（快速切三次会话，最后点的那一个赢；历史在路上的时候输入区锁住）。
> - **本阶段没有新增插件** —— 如实记一条，见上面那个修正块：这是核实后的结论，不是漏项。

**ADR-0016**：轨迹的 `arguments` / `result` **保留原文**（订单号、地址、余额进 PG）。这不是新开的口子 —— 帧里早就有了；ADR-0003 的「工具参数原文不出本进程」被澄清为「**不出到浏览器**」，它管展示层，本阶段管存储层。日志脱敏走**另一条路**（框架给 `Redactor` 协议 + 通用规则，业务注册字段路径 —— 结构化按字段类型打码，不靠正则碰运气，#26）。

**顺带搭两条车**（都是 issue 25 记下的小欠账，与 L3a 同在前端那条线上）：BFF 每次转发新建 `httpx.Client`（本机 ~690 ms，"横幅先蹦一下"的物理原因）· 历史响应的竞态（修法现成：照 `listRequests` 那道闸门再来一道 `historyRequests`）。

**L3b（人工确认，已详细规划为 issues 32–38）**：四块，**顺序不能换**：

1. **幂等持久化**（issue 32）：`db/README.md:142` 早已规划 `idempotency_keys` 表（**P1 只加这一张**）。键是 `(run_id, message_id, tool_call_id)` **三列** —— 上游每轮从 `call_0` 重新编号，少一列在多轮之间会撞。**ADR-0017** 改判了 `DESIGN.md` #17 那句"与工具超时要么一起做、要么都不做"：依赖方向是单向的（#15 → #17），而 #17 缺的从来不是"超时"这个调用方，是**任何**调用方 —— HITL 的恢复正是它等的第一个。
2. **`resume()` 记账**（issue 33）：issue 22 记下的欠账（续跑段落的帧 `run_id` 是 `None`）**从欠账升级为前置** —— HITL 的恢复**正是走 `resume()`**，不修这条，恢复段的帧全部脱离账本，成本与轨迹当场断链。
3. **HITL 挂起-恢复**（issue 34）：`Decision` 加第三个值（它现在的 docstring 直接写着「P0/P1 不做『需要确认』……#25 HITL 落地时再加」）+ 第 8 类流事件 `approval_required`（这个**名字**早在 `stream/utils/types.py:29-31` 的注释里预留了）+ `POST /runs/{run_id}/resume`（body 带 `decision` + 可选 `data`，**不拆成 approve/reject 两个端点** —— 拒绝也要恢复，把原因当工具结果回填）。**两条必须一起处理的边界**：**① 一次挂起只挂一条需审批的调用**（否则"一次确认配一份载荷"的归属说不清）· **② `SessionRegistry._busy` 的语义要从「运行中」扩成「运行中 *或* 有未决挂起」**，且判据必须落 **PG**（`status = needs_approval AND approved_at IS NULL`）不能只靠内存集合 —— 挂起时 run 会 `release`，而 `sessions.py` 自己写明部署是单进程、重启后内存集合清空。
4. **助手代付 + 下单前确认**（issue 35–37）：**ADR-0015** 定了密码的通路 —— 它走**一次性载荷**（`RunContext.payload`），与 `user_id` 完全同一条路，因此**永不进 wire schema、永不进消息、永不进 `arguments` / `result` 的落库原文**；并且 **`pay_my_order` 的工具 schema 里永远不出现 `payment_password` 这个参数**（这一条是前三条的*前提*：只要它进了 schema，模型就会编一个值填进去，而编的值会走 `arguments` 落库）。`CONTEXT.md` 的「支付密码」词条里那句「**助手不代付**（2026-09-21 决定）」**就此推翻** —— 当初的推理只对了一半：校验确实要明文经手，但它与「身份不进工具参数表」并不冲突。**ADR-0014**：挂起**不建审批表**，全部状态由 `charagent_tool_calls` 那一行 + 快照里的 `Suspension` 表达（`Suspension.approval_id` 保持 `None` —— 本项目永远不给它接线）。

> **L3b 的真机结论（2026-09-26，issues 32–38 收口）**：两条触发面都在浏览器里走通，五条否定断言逐条有结论。
>
> - **① 下单前的确认**（37）：说「下单吧」→ **只有两颗按钮、零个输入框**的确认卡 → 点确认 → 订单下成（`202609261603140000102499`）；点取消 → 模型如实告知"没有下成，车里那件还在"。
> - **② 助手代付**（35/36）：说「帮我把这单付了」→ 密码卡（**本人输**）→ 订单变 `paid`、余额 19505.00 → 19406.00。两种卡在请求体上就分得开：下单那张的 `data` 是 `{}`，付款那张带着载荷。
> - **③ 五条否定断言**：密码原文在**会话消息 / 轨迹参数 / 快照帧 / 两侧日志**四处一处都没有（正对照：订单号在消息与参数里都搜得到；全库**能按 `run_id` join 回来的 53 个列**扫下来，`payment_password` 这个名字**只**出现在 `charagent_tool_calls.approval_needs` 那一列 —— 那是"要问什么"的机器可读清单，不是值）· 同一个 `resume` 重放两次**不多扣一分钱、不多跑一次工具** · **进程重启之后**未决挂起仍然拦得住新提问（判据在 PG 不在内存；重启之后那张卡照样能取消、恢复段照样跑通）。
> - **④ 两条"核实后不做"**（记录"不做"与记录"要做"同样重要，否则下一次读规划的人会以为漏了）：**挂起超时** —— 挂起的 run 就挂着，用户可以用 `POST /runs/{run_id}/cancel` 收掉（挂起那一种也扩进去了）；加超时要先答"超时了怎么办"（降级？自动拒绝？），而那需要真实场景，与 ADR-0017 对 #15 的处置同一条理由。**角色分离**（`DESIGN.md` #25 那句"发起方不得审批自己发起的挂起项"）—— 本项目的发起方是**模型**、确认人是**买家本人**，不存在"自己批自己"；换成真人的多角色审批时才需要。
>
> 证据、逐条做法与清库记录在 `../.scratch/CharApp/issues/38`。

**L3 的十二片与依赖**（2026-09-24；`27` 是唯一的共同地基）：

| 片 | 内容 | 依赖 |
|----|------|------|
| `27` | **工具调用轨迹落库** —— L3a 的观测与 L3b 的挂起**都**落在它上面 | 无 |
| `28` | 成本口径 + `trace` 只读入口 | 27 |
| `29` | 日志脱敏（`Redactor` 协议 + 通用规则 + 业务注册字段路径） | 无 |
| `30` | 搭车：BFF 共享 client + 历史响应闸门 | 无 |
| `31` | **L3a 收口** | 27–30 |
| `32` | 幂等持久化（`idempotency_keys` 表 + PG store + 两处改判同步） | 无 |
| `33` | `resume()` 记账（**前置**，不是欠账） | 27 |
| `34` | HITL 挂起-恢复（`Decision` 第三值 + `approval_required` 事件 + `POST /runs/{id}/resume` + 单挂起规则 + `_busy` 语义扩展） | 27, 32, 33 |
| `35` | minimall 支付内部端点 + `pay_my_order`（schema 无密码参数）+ 护栏标注 | 34 |
| `36` | BFF 转发 + 前端确认卡（密码输入 / 刷新恢复 / 未决期间禁用输入） | 35 |
| `37` | 下单前确认（第二个触发面，纯「是/否」，复用 34–36） | 36 |
| `38` | **L3b 收口**（三条否定断言 + ADR + CONTEXT） | 37 |

关键路径：`27 → 33 → 34 → 35 → 36 → 37`。

**L4**（**尚未切 ticket，等 L3 收口后再规划**）：评估集（golden set 起步 + badcase 回流）· prompt A/B · **工具数量 A/B**：全挂 **17** 个（不是 9→17，L2 后的基线就是 17）vs `before_turn` 动态裁剪，用准确率差值决定 `#70` 的取舍 · 在 prompt 里要求模型**不复述**工具的真实数据（ADR-0003 的补充）· 「17」去数字化（35 处 / 10 文件）· 根 `CLAUDE.md` / `README.md` 同步。**新增一条前置**：L3a 的 `charagent_tool_calls` 是工具数量 A/B 的**对照数据源**（"工具选择正确率"要读它）—— 这条 A/B 之所以排在 L4 而不是更早，是因为它要的数据到 L3a 才有。**再加一条（2026-09-26，issue 37 落）**：prompt 的默认版本已从 `v2` 换到 **`v3`**（下单与付款都改由确认卡替模型问，第 1 条禁则改写）—— **L4 的评估与 A/B 一律以 v3 为基线**，v2 时期的跑分（含 L2 那次）与 v3 之后的**不可直接比较**；`v2` 保留不删，正是留给「prompt 层确认 vs 框架级确认」那次 A/B 的对照（回答 PRD §4.7 那个问题：有多少比例被用户一句话绕过去了）。

**L5（RAG + 注入防护，新增阶段；尚未切 ticket）**：`DESIGN` ⑧ 整册（#45–49，#45/#46/#47 是 P1）+ **#23 注入防护**。两条理由说明为什么是**新的一个阶段**而不是并进 L3/L4：

- **它是零排期的缺口**：这一轮核查发现 `RAG` 在 `PLAN.md` 与 `PRD.md` 里**一个词都没出现**，而用户的既定目标（`dev.prompt`）点名了「可观测性、**RAG**、安全、模型评估」四件 —— 另外三件都排上了，只有它没有。
- **它必须和注入防护一起做，而且那时做才对**：`DESIGN` #23 说注入危害成立要**三个条件同时具备** —— 不可信输入 + 私有数据 + 对外通信。今天三个是"两缺一"：没有 RAG，不可信输入只有用户自己说的话。**RAG 一上来就把外部文档灌进上下文，第三个条件当场补齐** —— 分开做等于先开门再装锁，顺序反了。

**实现成本比看起来低**：`project/charplot/rag/`（按类型调优切分 + Milvus 混合检索 + rerank + query rewrite）与 `project/menu/` 都有跑通的实现，L5 是"搬运 + 改造"。真正的新东西是**知识来源**（minimall 的商品与政策文档）与**溯源展示**（逐句引用）。

**顺带核实后不做的两项**（刻意记录，别留成沉默的缺席）：**#24 工具沙箱** —— 它的落点早在 2026-09-18 就修订成「agent 不直连业务库 + 出站目标白名单 + 身份不进签名」，三条现在**都成立**；**#30 SSRF** —— 出站目标只有 minimall 内部端点，攻击面不存在。将来真接了外部抓取类工具（如 L5 的联网检索），这两条要重新评估。

> **并入 L4 的一条旧尾巴**（issue 21 认领，别当漏项）：issue 14 记下的「`17` 这个数字被复述 **35 处 / 10 文件**」（Shotgun Surgery），治法是全仓去数字化 —— 它**押后到这里**，与上面那条「工具数量 A/B」一起做。理由：那次 A/B 必然要动工具集与计数语义（全挂 vs 动态裁剪说的就是「几个工具」），一次改写才划得来；而 L2.5 一整批**没有**动工具集，现在做等于为一个静态数字付一次全仓改写。改写落点届时是 `CharApp/minimall/tools.py` / `provider.py` / `cli.py` 的 `/help` 与 prompt 里那几处口径。

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

### 6.3 已记录的二十条 ADR

| # | 决策 | 日期 |
|---|------|------|
| 0001 | 内部端点直接采信调用方声明的 `X-User-Id` | 2026-09-19 |
| 0002 | 浏览器 → BFF 用 POST + `fetch` 读流，不用 `EventSource` | 2026-09-21 |
| 0003 | 工具事件在业务侧脱敏，不靠前端隐藏 | 2026-09-21（L2 规划期） |
| 0004 | 「退款中」做成订单状态的一个取值，接受两个状态机耦合 | 2026-09-21（L2 规划期） |
| 0005 | 身份说明只存引用不进帧正文；成本用归因列回答，不从总量里减 | 2026-09-23 |
| 0006 | 迁移历史在发布前压缩成一条；版本表兼作审计表（附带：写帧前会话行必须存在） | 2026-09-23 |
| 0007 | 删除一段会话是软删（行与消息都留着），「删除」在查询里只是一个过滤条件 | 2026-09-23 |
| 0008 | 一段对话有两种表示：账本 append-only、视图每轮现算、给人看的那份永不压缩 | 2026-09-23 |
| 0009 | 对话记录落在框架自己的 `db` 层，业务侧一行 SQL 都不写 | 2026-09-23 |
| 0010 | 会话搜索只搜标题（不搜消息正文）—— 删掉一个能力，换结果可解释 | 2026-09-23 |
| 0011 | 压缩的估算改成「启发式 + 固定开销校准」；触发判据量的是投影，不是账本 | 2026-09-23 |
| 0012 | 摘要失败就不切刀；只有上游报超限才允许硬截断 | 2026-09-23 |
| 0013 | 帧里的视图也走引用（帧 v7）：`metadata.view` 与进度同构 | 2026-09-23 |
| 0014 | **挂起不建审批表**：一次工具调用的状态就是审批单（`Suspension.approval_id` 永为 `None`） | 2026-09-24（L3 规划期） |
| 0015 | **代付的密码走一次性载荷**：永不进 wire schema、永不进消息、永不落库；`pay_my_order` 的 schema 里永远没有 `payment_password` | 2026-09-24（L3 规划期） |
| 0016 | **工具轨迹保留原文**：ADR-0003 的「不出本进程」澄清为「不出到浏览器」—— 它管展示层，存储层留原文 | 2026-09-24（L3 规划期） |
| 0017 | **幂等先做、工具超时推后**：改判 `DESIGN.md` #17 的「要么一起做、要么都不做」（依赖方向是单向的） | 2026-09-24（L3 规划期） |
| 0018 | **运行金额在收尾那一刻算好写死**；峰谷两套价按运行的开始时刻判（改判 0005 的「折算在查询侧」） | 2026-09-25（L3a · issue 28） |
| 0019 | **日志先脱敏再写**；访问日志整个关掉（搜索词会走查询串） | 2026-09-25（L3a · issue 29） |
| 0020 | **BFF 与客服服务共用一个进程级 HTTP 客户端**，代价是这条链路依赖单进程部署 | 2026-09-25（L3a · issue 30） |

`CharApp/docs/adr/0001-internal-endpoint-trusts-declared-user-id.md` —— 内部端点信任 `X-User-Id`。三条 ADR 条件均满足：改认证契约要动所有内部端点（难回退）· 面试官必问"为什么信任裸 user_id"（无上下文时反直觉）· 存在真实取舍（单机演示 vs JWT 签发/验签/时钟偏移/密钥轮换）。已记入 ADR：**身份来路**（session cookie 是唯一验证点，裸声明只是同一信任域内的转发）· **什么时候必须还**（判据是信任域被拆开，不是用户数）· **生产化路径 = 内部 JWT（`sub` claim）或 mTLS + 服务网格身份**（换的只是传输，验证点不变）· 代价说明 · 三个被否的替代方案（身份做成工具参数 / 现在就上 JWT / 每个端点各自校验）。

### 6.4 文档基线（已定）

**以当前磁盘状态为初始基线，不恢复任何已删文档。** `CharAgent/docs/adr/`、`docs/CONTEXT.md`、`docs/design/`、CharService 规划文档**永久废弃**，不再重建；`.scratch/` 保持为空。

**`CharAgent/docs/DESIGN.md` 是唯一例外，保留。** 理由：它在磁盘上（属于"当前状态"，不是需要"恢复"的东西），且是 `difficulties/` 14 份分册（已提交）的**唯一索引** —— 删掉它会让分册失去入口。它与 `difficulties/` 一起构成框架的「难点地图」，定位是参考资料而非规划文档，与本项目后续的 `CharApp/docs/PLAN.md` 互不覆盖。已于 `be2d17b` 纳入版本控制。

### 6.5 L3 规划期的核查发现（2026-09-24）

规划 L3 时对代码做了一次全量核对，带出**两个此前没人记录的缺口**。两条都不是"计划漏了一条"，是**只有读到代码才看得见**的东西，所以记在这里：

**① RAG 在路线图里零排期。** `grep RAG` 在 `PLAN.md` 与 `PRD.md` 里**零命中** —— 而用户的既定目标（`dev.prompt`）点名了「可观测性、RAG、安全、模型评估」四件，另外三件都有位置。它是 `DESIGN` ⑧ 整册（#45–49），其中三项标 P1。**处置：新增 L5**（理由与边界见 §5）。
> 顺带核实后**主动不做**的两项（见 §5 末）：#24 工具沙箱（落点已修订成三条，三条都成立）与 #30 SSRF（攻击面不存在）。**记录它们不做**与记录要做什么同样重要 —— 否则下一次读规划的人会以为漏了。

**② `SessionRegistry._busy` 管不住挂起。** 它现在的语义是「同一会话同一时刻只准有一次运行在跑」，而**挂起时 run 会 `release(thread_id)`** —— 于是挂起期间会话"不忙"，用户可以发新消息、新 run 会从最新快照起跑、当场撞上那个未决的 `Suspension`。**处置：落进 issue 34**，修法是把语义扩成「运行中 *或* 有未决挂起」，且判据必须落 **PG**（`charagent_tool_calls.status = needs_approval AND approved_at IS NULL`）—— `charagent_tool_calls` 那一行本来就是挂起态的家（ADR-0014），而 `sessions.py` 自己写明部署是单进程、重启后内存集合清空，只靠内存拦不住。

**③ 原定 L3 的七件事里，三件已被 L2.5 提前兑现**（明细见 §5 的 L3a 段）。这一条不是缺口，是**避免重复劳动**：规划时若照着 2026-09-19 那版 L3 全做一遍，会重写已经存在的东西。
