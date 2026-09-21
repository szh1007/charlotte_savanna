# 18 · 业务侧接入：Postgres 后端 + 记录装配 + 压缩参数 + BFF 转发

**Status:** ready-for-agent

**Type:** task

**Blocked by:** 16（压缩参数）、17（记录与水合）

**上游:** `../PRD.md` §4.9（L2.5 行）、`CharApp/docs/PLAN.md` §5（L2.5）

## 做什么

把框架侧新增的三样能力接到业务上，一共四件小事：

1. **快照后端换 `postgres`**（内存只作前期测试用；库里建表这一步**已经完成**）
2. **装记录**：`ConversationRecorder` 接进唯一的装配处（CLI 与服务都要过）
3. **配压缩参数**：`CHARAPP_CONTEXT_*` 五个环境变量 → `ChatSession`
4. **BFF 转发 `GET /conversations`**（前端下一片用）

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| 快照后端由环境变量决定：服务路径**永远**走 `checkpoint_saver_from_env()`（server 构造的 `CliOptions` 不带 `backend`） | `CharApp/minimall/server.py:268-273`、`CharAgent/client/app.py:262-273` |
| `build_saver` 支持 `memory` / `redis` / `postgres`，未知值抛 `CheckpointConfigError` | `CharAgent/checkpoint/config.py:99-121` |
| 相关变量名与默认值都在一个 docstring 表里（`CHARAGENT_CHECKPOINT_*` 六个 + 两个复用项） | `checkpoint/config.py:61-76` |
| 当前值是 `CHARAGENT_CHECKPOINT_BACKEND="memory"` | 根 `.env:74`、`.env.example:74` |
| Postgres 连接：`CHARAGENT_DB_DSN`（可带可不带驱动后缀）为空时，用共用的 `PGSQL_USERNAME/PASSWORD/HOST/PORT/NAME` 兜底 | `checkpoint/config.py:167-185` |
| **本机 PG 的 `charagent` 表已经建好了** —— 2026-09-22 实测：`charagent_threads` / `_runs` / `_messages` / `_tool_calls` / `_checkpoints` / `charagent_alembic_version` 六张全在（库 `charlotte`），所以**换后端不需要跑任何建表动作** | 实测（`PGSQL_NAME=charlotte`） |
| 装配**只有一处**：`MinimallService.session_for(context, *, event_sink)`，CLI 与服务都经过它 | `CharApp/minimall/service.py:228-279` |
| `LoopGuard` 的三个默认值常量与构造点（新参数照这个形状传） | `service.py:58`、`:70-71`、`:266-270` |
| `config.py` 的既有惯例：`ENV_*` 常量 · `*_from_env(env=None)` · 首行 `values = os.environ if env is None else env` · 读值 `(values.get(ENV_X) or "").strip()` · 缺值/坏值抛 `MinimallConfigError` | `config.py:25-46`、`:75-92`、`:107`、`:129` |
| `ServerConfig` 是 `@dataclass(frozen=True, slots=True)`；`token` 字段带 `repr=False`（共享秘密不进 traceback） | `config.py:57-72` |
| BFF 的三个服务头**只在一处**造：`_service_headers` | `app/minimall/views_bff.py:557-568` |
| BFF 的读历史已经是 POST（浏览器面）+ 下游 GET，且**会话编号不进 URL**（走 `X-Conversation-Id` 头） | `views_bff.py:738-777`、`:931-981` |
| BFF 的身份只取 `request.user.pk`，请求体影响不了它 | `views_bff.py:957` |
| BFF 不 import CharApp（生产代码零依赖，只有一处测试 import） | `views_bff.py:79-81` |
| 有一条用例守着「`.env.example` 必须列出业务读的**每一个**变量」 | `CharApp/tests/test_server.py:968` |
| `.env` 里 `CHARAPP_SERVER_URL` / `CHARAPP_BASE_URL` 等已就位（服务 1007，Django 8000） | 根 `.env:58-64` |

## 具体任务

### 1. 快照后端换 Postgres

- 根 `.env` 与 `.env.example`：`CHARAGENT_CHECKPOINT_BACKEND` 从 `memory` 改成 `postgres`；`CHARAGENT_DB_DSN` 留空（走共用的 `PGSQL_*` 兜底）
- `.env.example` 的注释里写清：**内存后端只用于早期测试**；一旦切持久化，`/history` 与模型上下文的行为都随之改变（这正是 issue 17 要接线的那件事）
- 真机验证：「聊两句 → **重启 CharApp 进程** → 历史还在」是 L2.5 的核心验收，**必须在切了后端之后才做**

### 2. 装记录（唯一的装配处）

- 在 `session_for` 里构造一个绑定了 `(tenant_id, user_id)` 的 `ConversationRecorder` 交给 `ChatSession`（或按 issue 17 定的形状）
- `tenant_id` 取值：服务入口 `"minimall"`，CLI 入口 `"minimall-cli"`（**副产品**：网页左栏不显示 CLI 的会话，这是想要的效果，写进注释）
- `user_id`：服务入口从 `X-User-Id` 来（`buyer_id(context)`，已有），CLI 入口从 `--user-id` 来（已有）
- **顺手把 issue 13 留的那条小尾巴做掉**：`session_for` 的 `redact` 现在是"靠人记得包"的隐式约定（注释里明说「第三个入口也得包」的线索只在 docstring 里）。把它做成一个**必填参数**（`redact: bool`），把选择摆到台面上 —— 现在正好有两个入口要改，是动手的时机

### 3. 压缩参数（`CHARAPP_CONTEXT_*`）

照 `config.py` 的惯例加一个 `context_config_from_env()`（返回一个 frozen dataclass，形状照 `ServerConfig`）：

| 变量 | 建议默认 | 管什么 |
|------|---------|--------|
| `CHARAPP_CONTEXT_MAX_TOKENS` | `32000` | 单请求上下文预算（触发压缩的阈值） |
| `CHARAPP_CONTEXT_KEEP_TURNS` | `6` | 最近几轮完整保留 |
| `CHARAPP_CONTEXT_TOOL_LIMIT` | `2000` | 更老的 `tool` 消息正文截断到这个字符数 |
| `CHARAPP_CONTEXT_SUMMARY` | `on` | 摘要开关（关掉退化成纯裁剪 + 工具结果截断） |
| `CHARAPP_CONTEXT_WATERMARK` | `0.7` | 压到这个比例以下才停手（`0 < x < 1`，越界抛 `MinimallConfigError`） |

- 水位线比例这类"必须落在区间里"的值要**显式校验**（照 `thinking_from_env` 对未知值的处理风格）
- 全部有默认值 ⇒ 不填也能跑（与 `CHARAPP_THINKING` 的空值语义一致：不填 = 用默认）
- `.env.example` 同步补上（`test_server.py:968` 那条用例会守）

### 4. BFF 转发 `GET /conversations`

- 新增一个视图 + 路由：形状照 `AgentHistoryView`（**POST + 请求体 + CSRF**，因为浏览器面不让会话编号或用户身份进 URL —— ADR-0002 与 issue 13 的既有纪律），下游那一跳是 `GET /conversations`，身份走 `_service_headers` 那三个头
- 返回体原样透传（BFF 不解析上游 JSON，与 `forward_history` 一致）
- 未配置 token / 连不上 → 沿用既有的用户话术表（`ERROR_COPY` 那条路）

## 验收

- [ ] `.env` 与 `.env.example` 都改成 `postgres`；`.env.example` 补上 `CHARAPP_CONTEXT_*` 五个
- [ ] **真机：聊两句 → 重启 CharApp 进程 → 前端历史还在，且模型接着上文答**（这条是 L2.5 的核心验收，缺它整片不算完成）
- [ ] 服务与 CLI 两个入口都装上了记录（各跑一次，`charagent_messages` 里都留下行）
- [ ] CLI 的记录 `tenant_id="minimall-cli"`，网页的是 `"minimall"`；网页左栏（下一片）看不到 CLI 的会话
- [ ] `session_for` 的 `redact` 已成必填参数；两个入口都显式给出
- [ ] 压缩参数：不填能跑（用默认）；填了坏值（比如水位线 `1.5`）启动期就报错，且错误信息是中文一句话
- [ ] 真机：故意用一个小阈值把压缩逼出来 → 单请求 token 不再增长（看日志或 `context_compacted` 事件），**同时用户看到的历史一条不少**
- [ ] `POST /minimall/agent/conversations/` 走 POST + CSRF，返回当前登录买家的会话列表；换一个买家登录看不到别人的
- [ ] 上游未配置 token / 连不上 → 用户看到一句中文话术（不是 500 裸奔）
- [ ] 既有三套测试全绿；`ruff` 干净

## 备注

- **本片不含前端**：左栏列表与「新对话」按钮是 issue 19；会话管理动作（重命名 / 删除 / 搜索 / 置顶）是 issue 20。本片只把数据通路铺到 BFF 为止
- **换后端这一步很小，但它改变的东西很大**：内存后端下「重启即清空」是常态，切了 Postgres 之后历史变成**真正长期累计** —— 所以 issue 16（压缩）要在本片之前落地，否则等于把成本问题放大一档
- **建表已经就绪，但别假设部署时也一样**：换环境要跑 `alembic upgrade head`（`CharAgent/alembic.ini`），这条写进 `sh/charapp_backend.sh` 的注释或子项目 README
