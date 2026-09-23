# 18 · 业务侧接入：Postgres 后端 + 记录装配 + 压缩参数 + BFF 转发

**Status:** done

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

- [x] `.env` 与 `.env.example` 都改成 `postgres`；`.env.example` 补上 `CHARAPP_CONTEXT_*` 五个
- [x] **真机：聊两句 → 重启 CharApp 进程 → 前端历史还在，且模型接着上文答**（这条是 L2.5 的核心验收，缺它整片不算完成）
- [x] 服务与 CLI 两个入口都装上了记录（各跑一次，`charagent_messages` 里都留下行）
- [x] CLI 的记录 `tenant_id="minimall-cli"`，网页的是 `"minimall"`；网页左栏（下一片）看不到 CLI 的会话
- [x] `session_for` 的 `redact` 已成必填参数；两个入口都显式给出
- [x] 压缩参数：不填能跑（用默认）；填了坏值（比如水位线 `1.5`）启动期就报错，且错误信息是中文一句话
- [x] 真机：故意用一个小阈值把压缩逼出来 → 单请求 token 不再增长（看日志或 `context_compacted` 事件），**同时用户看到的历史一条不少** —— 实测读作「不再**线性**增长」：连问 7 轮，单请求输入稳在 6.0k 上下（+30/轮，来自滚动摘要自身），而账本已 17 条消息；历史一条不少已核（3 问 3 答全在）。摘要那一半在真机上一直降级，见开放项 1
- [x] `POST /minimall/agent/conversations/` 走 POST + CSRF，返回当前登录买家的会话列表；换一个买家登录看不到别人的
- [x] 上游未配置 token / 连不上 → 用户看到一句中文话术（不是 500 裸奔）
- [x] 既有三套测试全绿；`ruff` 干净

## 备注

- **本片不含前端**：左栏列表与「新对话」按钮是 issue 19；会话管理动作（重命名 / 删除 / 搜索 / 置顶）是 issue 20。本片只把数据通路铺到 BFF 为止
- **换后端这一步很小，但它改变的东西很大**：内存后端下「重启即清空」是常态，切了 Postgres 之后历史变成**真正长期累计** —— 所以 issue 16（压缩）要在本片之前落地，否则等于把成本问题放大一档
- **建表已经就绪，但别假设部署时也一样**：换环境要跑 `alembic upgrade head`（`CharAgent/alembic.ini`），这条写进 `sh/charapp_backend.sh` 的注释或子项目 README

---

## 实际开发情况 2026-09-23

### 一、开工前核对：四件里有两件半已经被做掉了（ticket 与代码对不上）

| ticket 说 | 实际 | 处置 |
|---|---|---|
| 任务 2「装记录」要接进 `session_for` | **issue 17 实施时已经接了**：`service._recorder_for` + `TENANT_WEB` / `TENANT_CLI` 都在，两个入口也都传了 | 不重做；只做它点名的那条尾巴（`redact` 必填） |
| 任务 1「`.env` 当前是 `memory`」 | 真机跑 issue 17 验收时已经改成 `postgres` | `.env` 只补注释；`.env.example` 才是真要改的 |
| 任务 3 的五个旋钮里有 `CHARAPP_CONTEXT_SUMMARY` | **框架侧没有这个能力**（见开放项 1） | 在框架的 `TrimAndSummarize` 上补一个 `summarize` 字段 |
| 任务 4「形状照 `AgentHistoryView`」 | 照抄得到，但它连请求体都不该读（列表不针对某一段对话） | 视图不读请求体；`X-Conversation-Id` 给空值（见开放项 2） |

### 二、拍板的开放项（ticket 没定 / 与代码对不上，实现时定下来的）

1. **「摘要开关」只能先在框架侧落地，业务侧才接得上**。ticket 的表里给了 `CHARAPP_CONTEXT_SUMMARY=on`（「关掉退化成纯裁剪 + 工具结果截断」），但框架里没有这个开关：`AgentLoop._compile_view` **恒**把主模型当摘要模型传进去（`apply(summarizer=self._model)`），于是业务端 `TrimAndSummarize(summarizer=None)` 兜不住（`self.summarizer or model` 会退回主模型）。做法：`TrimAndSummarize` 加 `summarize: bool = True`，`False` 时跳过摘要那一步且 **`warning` 保持 None** —— 那个字段是留给「本来要摘要却没成」的，被一个配置项长期占着会让每一次压缩看起来都出了岔子。默认 True ⇒ 既有行为逐字不变。**这是框架侧的改动落在业务片里，单独点名。**
2. **`redact` 的落点**：`MinimallService.session_for(context, *, event_sink, redact)` —— 包裹动作在装配处（`redacting_sink` 移进 `service.py`），入口只负责**说出自己的出口是不是浏览器**（web 给 True、CLI 给 False）。为什么不是「入口自己包好再传」：那样这个参数仍只是一句口头约定，包没包从调用点看不出来；现在是必填 keyword，漏了当场 `TypeError`。`redaction.py` 与 `server.py` 里那两段「为什么放在服务入口」的说明同步改写成新形状。
3. **列会话那条路的 `X-Conversation-Id` 给空值**，而不是省掉这个头：三个头是一组（`_service_headers` 的唯一性正是被「少带一个」害过），而这条路由只按租户与买家过滤，上游对空/缺的头兜成它自己的默认段 —— 值是什么都不影响结果。日志前缀为此改成可省（`_who_for(user_id)` 不再拼一个空段）。
4. **越界值的校验放在 `config.py`**：水位线写 `1.5` 这类错如果留给框架发现，抛的是 `CompactionConfigError`，而它**不在** `STARTUP_ERRORS` 里 —— 那就是 traceback 糊一屏。业务这一处拦下，错误信息带变量名与实际值，`main()` 照常翻成一句中文；框架那边的 `__post_init__` 校验保留（程序化使用时的最后一道）。
5. **`.env` 只补注释**（五个旋钮 + 手动逼压缩的两行示例），值一律留空 = 走默认。真机逼压缩不用改这个文件，起进程时带环境变量即可（`load_root_env` 是 `override=False`）。

### 三、碰过的文件

| 文件 | 改动 |
|---|---|
| `CharAgent/agent/compaction.py` | `TrimAndSummarize` +`summarize: bool = True`；`apply` 关掉时跳过摘要（`warning` 保持 None） |
| `CharAgent/tests/test_loop_compaction.py` | +2：关掉时只裁剪且**一次调用都不发**；已有摘要不被开关拿掉 |
| `CharApp/minimall/config.py` | `ENV_CONTEXT_*` / `DEFAULT_CONTEXT_*` 各五个 + `ContextConfig`（frozen dataclass）+ `context_config_from_env()` + 三个读值小函数 |
| `CharApp/minimall/service.py` | `MinimallService.context` 字段；`session_for` 的 `redact` 必填 + 出口包裹；`build_compaction_for()`（策略 + 估算器成对造，**估算器每会话一份**） |
| `CharApp/minimall/server.py` | `build_service` 读 `context_config_from_env()`；`MinimallSessions.provide` 传 `redact=True`（不再自己包 sink） |
| `CharApp/minimall/cli.py` | `service_for` / `build_session` 透传 `context`；CLI 显式给 `redact=False` |
| `CharApp/minimall/redaction.py` | 「放哪儿」的说明改写：开关是必填参数，不再是「靠人记得包」 |
| `app/minimall/views_bff.py` | `CONVERSATIONS_PATH` / `CONVERSATIONS_TIMEOUT` / `NO_CONVERSATION_ID`；`_conversations_url()` / `forward_conversations()` / `AgentConversationsView`；`_who_for` 的会话段可省；文件里「三条路」的说法跟着改成四条 |
| `app/minimall/urls_bff.py` | 一条路由 `conversations/` |
| `.env.example` | 后端换 `postgres` + 「memory 只用于早期测试」「两样都要持久」的说明；补 `CHARAPP_CONTEXT_*` 五个 |
| `.env` | 补注释（值留空） |
| `sh/charapp_backend.sh` | 前置里补 `cd CharAgent && alembic upgrade head`（换环境先建表） |
| 测试 | 新增 `CharApp/tests/test_compaction.py`（4 例：旋钮映射 / 阈值进到 wire / 摘要开关 / `context=None` 不压）；`test_server.py`（+3 配置例、模板清单 +5 个名字、`build_service` 断到 `context`）；`test_bff.py`（+8：转发契约 / 两个买家 / 身份 / 不可达 / 没配令牌 / 上游码 / 动词 / CSRF）；既有 7 处 `session_for` 调用点补 `redact=` |

### 四、真机验收（2026-09-23 上午，Claude 代跑）

环境：Django `runserver 8000 --noreload`（新实例）+ CharApp 服务（新实例，含两次重启）+ 本机 Postgres/MySQL/Redis + 真 DeepSeek；会话 `acceptance18`（买家 2 = charlotte）。BFF 那一跳用真实 session cookie + CSRF（`manage.py shell` 造 session，curl 打 `/minimall/agent/...`）。

| 步骤 | 结果 |
|------|------|
| ① 第 1 句：种下「我叫林小满、幸运数字 47」 | `final → 记住了`；`charagent_runs` 1 行（in 5675 / total 5712） |
| ② 第 2 句「我余额还有多少？」（服务带 `CHARAPP_CONTEXT_MAX_TOKENS=800` 起） | 工具真打到商城 → `你余额还有 **600000.00 元**`；流里 **2 条 `context_compacted`**（`dropped=2`，第一条 `summarized=true`） |
| ③ **重启 CharApp 进程**（1007 释放后再起，换回默认阈值） | 内存全空 |
| ④ 第 3 句「我叫什么名字？我的幸运数字是几？」 | `final → 你叫**林小满**，幸运数字是 **47**` —— 只可能来自上一段进程；该次 `input_tokens=5876`（比第 1 句多约 200，正是水合回来的那 5 条短消息） |
| ⑤ `/history`（经 BFF） | 6 条可见消息逐条拉回（3 问 3 答），库里该会话共 20 行（工具与中间轮 `hidden=true`） |
| ⑥ 再问 4 句（阈值 800） | 单请求输入稳在 6.0k 上下（6025 / 6056 / 6085 / 6116），而账本已 17 条消息；压缩事件 `dropped=16`、压后视图估算 2887 |
| ⑦ 列会话（BFF，买家 2） | `[{"conversation_id":"acceptance18","title":"请记住：…"}]` |
| ⑧ 列会话（BFF，买家 10 = savanna） | `{"conversations":[]}` —— 换一个买家看不到别人的 |
| ⑨ CLI 入口跑一句 | `[完成] 答完了 · 1 轮 · 5681 tokens · 快照 PostgresCheckpointSaver 里 1 帧`；库里 `tenant_id='minimall-cli'`、线程 `minimall:2:cli18`、2 行消息 + 1 行 runs |
| ⑩ 再看买家 2 的网页列表 | 仍只有 `acceptance18` —— **CLI 的会话没混进来**（租户隔离在真机上成立） |

### 五、真机抓到的缺陷（未修，留给拍板）

**摘要在真机上一直降级**：服务端日志一行行「摘要模型没有给出正文, 本次只做裁剪」，事件里 `summarized=false` + `warning` 那句。

- **根因（已定位并验证）**：摘要那次调用 `chosen.generate(request, None, max_tokens=512)` **没传 `thinking`** → 落到适配器的构造默认值 → `.env` 的 `CHARAPP_THINKING` 是空 = 不传 = 上游默认**开启**。512 的预算被推理吃掉，正文为空 → 框架按「摘要失败」降级纯裁剪。
- **验证方式**：临时把那一次调用改成 `thinking=False`（只诊断，未进代码、已还原）→ 同一段对话立刻 `summarized=true, warning=null`。
- **为什么没顺手改**：这是框架侧的事（issue 16 的交付物与思考模式相互作用），修法至少有三种（摘要调用固定关思考 / 抬高 `summary_max_tokens` / 给业务一个旋钮），属于要拍板的选择。
- **影响面**：压缩的**裁剪**那一半照常生效（请求不再随对话线性增长），损失的只是「压得更狠」与滚动摘要的记忆；用户看不到任何异常 —— 这也是它一直没被发现的原因。

### 六、开放项

| # | 项 | 说明 |
|---|---|---|
| 0 | ~~两张表的 `run_id` 撞名且无关联~~ | 2026-09-23 查库发现（见 §十）。**已切 issue 22**（帧上那个更名 `loop_id` + 帧→运行外键 + 运行→帧溯源列） |
| 1 | ~~摘要与思考模式打架~~ | 见上。**2026-09-23 已修**（用户拍板：摘要固定关思考 + 预算 1024），见 §九 |
| 2 | 每轮仍有约 +30 token 的缓慢增长 | 来自滚动摘要自身（每轮重压、缓慢变长，受 `summary_max_tokens=512` 约束）。与「随对话线性增长」不是一回事 —— 验收里那句「不再增长」严格说应读作「不再线性增长」 |
| 3 | 列会话的空 `X-Conversation-Id` | 目前是注释里的约定（「上游对空/缺的头兜默认段」）；有朝一日上游要求这个头非空，这条会先红（用例断的就是 `""`） |
| 4 | BFF 没为列表新增错误码 | 走的是与读历史同一套（`agent_unavailable` 等）；上游将来为列表新增码时，`ERROR_COPY` 要跟着补 |

### 七、验证到哪一步

`pytest` 全绿：CharAgent **984 passed / 74 deselected**（另跑标记集 `-m "pg or pg_db or redis"` **63 passed**）· CharApp **198 passed** · Django BFF **67 passed**；`ruff check` / `ruff format --check` 干净（全仓）。真机十条见上，含 L2.5 的两条核心验收。

### 八、代码审查改了什么（Standards / Spec 两轴各起一个 sub-agent，2026-09-23）

**改掉的（5 处）**：

1. **`MinimallService.context` → `compaction`**（Standards：一名两义）：同一个类里已经有 `session_for(self, context: RunContext, …)`，`self.context` 却是另一样东西 —— CLI 那边已经被迫把局部量改名成 `run_context` 才写得下去，这就是撞名的证据。配置层的名字**不动**（`ContextConfig` / `context_config_from_env()` / `CHARAPP_CONTEXT_*` 都是 ticket 点名的）。
2. **`_context_bool` 的报错话术**（Speculative Generality）：它带 `default` 参数，话术却把默认写死成「(不填 = 开)」—— `default=False` 时那句就是假的。改成「(不填 = 用默认值)」。
3. **`keep_turns` 的 docstring 补交叉引用**：同一个「轮」在 `service.max_turns` 那里是另一次含义（一次运行内最多几次模型决策），写明两者不同级（CONTEXT.md 把这里这个量叫「运行」）。**名字保留** —— 环境变量名是 ticket 定的，字段跟着它走才不会名实分离。
4. **`CharApp/tests/test_compaction.py` 不再自带第 3 份 `mall_client`**（Duplicated Code）：改用 `conftest.py` 已有的 `client` fixture。
5. **`server.create_minimall_app` 的 docstring「三条路」→「四条路」**（Standards 顺带指出，属 issue 17 留下的旧话）：补上「列会话只在给了记录库时才注册」这半句，免得与启动日志那行「四条路」继续自相矛盾。

**看了但不改的（记理由）**：

- **`forward_history` 与 `forward_conversations` 逐段同形**（Standards 建议抽 `_forward(...)`）：抽出来要传 6 个参数（url / timeout / action / who / conversation_id / 日志前缀），签名比它替换掉的两段正文还长；而本文件既有的形状就是「一条路一个函数、各自的失败表写在 docstring 里」（`forward_cancel` 与它们并不相同：404 要原样转给浏览器）。三处里只有两处相同，够不上本仓「重复 ≥ 3 次才抽取」那条线。
- **`_conversations_url()` 与 `NO_CONVERSATION_ID = ""`**：前者跟着 `_runs_url` / `_cancel_url` / `_history_url` 的既有写法（一条路一个单行函数）；后者是 **wire 上的值**（字符串），与 `_who_for(conversation_id=None)` 那个「日志里不拼会话段」是两层的两个哨兵，合并反而会让日志多出一个空段。
- **`build_compaction_for` 返回元组**（Data Clumps，弱项）：不变量（成对）没进类型。等第三个零件出现时再打包成值类型 —— 现在它只是一个 8 行函数里的一条 return。
- **Spec 轴标出的两条「未要求」**：`_context_int` 的 `>=1` 校验（规则与框架 `__post_init__` 逐条一致，多拦一道不改变语义）；框架侧那个 `summarize` 字段（ticket 的表要求的语义，而代码里没有对应能力 —— 已在开放项 1 点名）。
- **Spec 轴指出验收框 7 原本勾得过满**：已就地补注解（写成「不再**线性**增长」并指向开放项 1/2），而不是只在下面这段记录里解释。

### 九、追加修复：摘要固定关思考 + 预算 1024（2026-09-23，用户拍板）

§五 那个缺陷的处置 —— **摘要那一次调用固定 `thinking=False`**（钉死的常量，不是参数），`DEFAULT_SUMMARY_MAX_TOKENS` 512 → **1024**。

- **为什么是常量而不是开关**：漏掉它的代价是「整片功能静默失效」（见 §五）。留一个 `summarizer_thinking=` 字段，等于把这个坑原样留给下一个接进来的人 —— 而摘要是机械压缩，思考在这件事上买不到什么。真要开，改一行即可。
- **为什么 1024**：摘要是**滚动**的（上一条连新裁掉的段一起重压），这个数同时是「单次装得下多少」与「摘要最终能长到多大」；关掉思考之后，1024 装得下一段带事实与结论的中文摘要。
- 落点：`CharAgent/agent/compaction.py`（常量 + 那一次调用的两个参数 + 类与 `_summarize` 的说明）、`CharAgent/docs/DESIGN.md` 的 #7 补一句、用例 +2（断言那一次调用 `thinking is False` 且带自己那份预算；默认值 1024 被钉住）。**业务侧一行没改** —— 那五个 `CHARAPP_CONTEXT_*` 旋钮不动。
- **真机复验**（同一个「必压」配置：阈值 800 / 水位线 0.5；直接打服务，会话 `fix18`，买家 2）：三句话，第二句起每次压缩都是 `summarized: true, warning: null`；库里 `summary_covers` 0 → 5 → 9 逐帧递进，摘要是**一段真的中文事实**（余额 600000.00 元 + 13 笔订单的编号 / 金额 / 日期）；第三句「结合上面两件事用一句话总结」的答复只可能来自那条摘要（那几轮的原文已不在视图里）。记录表 15 行 / 6 行可见 —— 用户看到的历史照旧一条不少。
- 框架侧全量：**986 passed / 74 deselected**（比修复前 +2）。

> 这条修复与本片原定的「业务接线」是两件事，但它**由本片的真机验收暴露**、而不修则压缩的第三件套在生产里等于不存在 —— 记在这里，改动本身是框架侧的一小处（与 §二 的 `summarize` 字段同族）。

### 十、查库查出来的第三件：两张表的 `run_id` 撞名（2026-09-23，用户提问触发）

用户查 `charagent_checkpoints` 与 `charagent_runs` 时发现 **两个 `run_id` 一个都对不上**。核对下来是三件事叠在一起：

| 层 | 谁生成 | 语义 | 库里能 join 吗 |
|---|---|---|---|
| 传输层（SSE / 取消句柄） | `server/runs.py::new_run_id()` | 一次 HTTP 运行 | ❌ 从不落库（`new_run_id` 的 docstring 写明「与 loop 写进快照的不是一个东西」） |
| 快照层（帧上那个） | `agent/loop.py` `uuid4().hex`，resume 沿用 | 一次循环执行 | ❌ 与运行行无关联列 |
| 记录层（运行行那个） | `db/repositories/runs.py` `uuid4().hex` | 一行账 | ✅ 自家 `messages` / `tool_calls` 都指向它 |

- **传输层 vs 快照层**：写明的有意设计（同名不同物，各管一段），不改。
- **快照层 vs 记录层**：**缺一条关联** —— 而且帧上那个编号全仓只有 `resume()` 一处读它（沿用），是个「写了没人查」的标识；「哪几帧属于这次执行」今天靠 `loop_id` 自己分组，「哪一帧属于哪行账」谁也答不上。
- 用户拍板：**不接受不同表的同名字段表达不同意思** → 帧上那个更名 **`loop_id`**、帧新增指向运行行的 **`run_id`** 外键、运行表新增 **`last_checkpoint_id`** 溯源列，并选了「编号在运行开始前定下来」（B1，即仓储层文档里原本就推荐的那条路）。
- **已切 issue 22**（`22-framework-loop-id-and-run-linkage.md`），含存储格式 v5 → v6 与记录员两段式。

> 记录人：Claude Code (charlotte) · 2026-09-23
