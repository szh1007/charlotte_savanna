# 13 · 展示层：工具事件去字段化 + 会话历史

**Status:** ready-for-agent

**Type:** task

**Blocked by:** 12（严格说只有后半段依赖它，见备注第一条）

**上游:** `../PRD.md` §4.9（L2）、`CharApp/docs/adr/0003`

## 做什么

两件让**浏览器看到的东西**变得正确的事：

1. **工具事件去字段化** —— `tool_call` / `tool_result` 的 `arguments` / `summary` / `error` 换成人话 `label`，**敏感数据不出 CharApp 进程**（ADR-0003）
2. **会话历史** —— 刷新页面 / 换标签页之后，对话还在

## 第一件：去字段化

落点是**业务侧包装 `event_sink`**（三层取舍见 ADR-0003）：

- 业务本来就持有 sink：框架的 `SessionProvider.provide(context, *, event_sink)` 把事件出口交给业务，业务再递给 `ChatSession`。**业务站在唯一出口上，包一层就是脱敏** —— 框架与 Django BFF 一行不改
- 在包装层里换掉 `data`：`{"label": "正在查询订单"}` / `{"label": "订单获取成功"}`
- **保留** `tool_name` + `tool_call_id` / `duration_ms` / `turn`（ADR-0003 的表格逐字列了哪些改哪些不改）
- **不动 `reasoning`** —— 那是模型自己的话，遮掉它会让 L3 的「当时它看到了什么」不可解释
- **不留演示开关** —— 「演示时把敏感数据打开」的环境变量是安全反模式
- 标签表放业务侧（`{tool_name: 中文短语}`），**17 个工具都要有**，且**未命中的工具要有兜底话术**，不能漏出一个英文工具名

## 第二件：会话历史

- **框架侧**：`CharAgent/server/` 新增**只读**历史端点。会话在 `SessionRegistry` 里按 `thread_id` 长驻，只差一个读口 + 一条路由
- **BFF**：`/minimall/agent/history/` 转发（身份仍**只从 session 取**，与 issue 06 同一条纪律）
- **前端**：`agent.html` 页面加载时拉一次，把历史渲染出来

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| 事件载荷形状：`tool_call` = `{tool_call_id, tool_name, arguments, status, turn}`；`tool_result` = `{tool_call_id, tool_name, status, duration_ms, turn, summary\|error}` | `agent/utils/events.py:64-94` |
| **`StreamEvent` 是 `@dataclass(slots=True)`，不冻结，`data` 是自由字典 —— 可原地改写** | `stream/utils/types.py:51-69` |
| 业务侧本来就持有 sink | `CharAgent/server/utils/types.py` 的 `SessionProvider.provide(context, *, event_sink)`（`:133` 起） |
| `tool_result` 的 `summary` 是工具返回值**截断到 200 字符**的正文（订单号、地址、余额都在里面） | `stream/utils/types.py:46-47`、`agent/utils/events.py:59-61` |
| 框架 `server/` 现在只有 `POST /runs` 与 `POST /runs/{run_id}/cancel`，**没有**会话的 HTTP 路径 | `server/app.py:132`、`:176` |
| 会话按 `thread_id` 长驻在 `SessionRegistry`（`acquire` / `release`） | `server/sessions.py:144`、`:183` |
| BFF 的 `relay()` 现在逐帧**字节透传**，不改写 | `views_bff.py:651` |
| BFF 的身份只取 `request.user.pk`，不看请求体 | `views_bff.py:777`、`:831` |
| 页面现在刷新即新对话；`conversation_id` 每个标签页生成 | `templates/minimall/agent.html`、issue 06 的已知边界 |
| `thread_id = minimall:{user_id}:{conversation_id}`，框架对**整串**有 128 字符校验 | issue 06 的「计划外但必须做的」第 3 条 |

## 具体任务

1. **CharApp 侧包装 `event_sink`**（在 `service.py` 的装配处 —— **只有一处**，CLI 与 server 都要过）+ 标签表
2. **前端 `agent.html` 事件渲染改成读 `label`**（事件形状变了，不跟着改就是白屏）
3. **框架 `server/` 加只读历史端点** + 测试 + 根门面防漂移测试与通用性测试跟着走
4. **BFF 加历史转发端点**
5. **前端加载时拉历史并渲染**
6. 写测试

## 验收

- [x] **浏览器 devtools 的网络面板里**，SSE 帧的 `data` 搜不到任何后端字段（订单号 / 地址 / 余额 / slug / 金额）—— 真机核过：`tool_call` 只剩 `{tool_call_id, tool_name, status, turn, label}`，`tool_result` 剩 `{..., duration_ms, label}`。**边界**：这条管的是**工具事件**；模型自己在 `reasoning` / `final` 里复述的值照旧在帧里（ADR-0003 的取舍，已补进 ADR）
- [x] 页面上工具行显示的是**中文短语**，且 17 个工具都有话说（没有兜底漏出英文名）—— 表与工具集一一对应有用例钉；兜底那句查的是「不含拉丁字母」而不是「不是纯 ASCII」
- [x] `tool_name` 仍在事件里（演示时讲得出「模型选了哪个工具、有没有选错」）
- [x] **刷新页面 → 对话还在** —— 浏览器里真刷过（第一遍没成，见下「真机抓到的那条」）
- [x] **换标签页 → 各自的历史各自恢复**（issue 06 用户故事 25 的延伸：各聊各的，现在还要各自记得住）—— 浏览器里开了第二个标签页核过：两个会话编号各自一份历史
- [x] **买家 A 拉不到买家 B 的历史**（身份从 session 取；请求体里塞别人的 `user_id` 无效）—— BFF 与框架各一条守卫用例（请求体 / 请求头都影响不了身份）
- [x] 历史端点**只读**：任何写意图（POST / DELETE）→ 405 或 404，且**不产生任何运行** —— 框架那条路由只登记 GET（四种方法逐个钉过），并断言会话表与运行表都是空的。**BFF 那侧反过来**：它只认 POST（浏览器面的那条，见下表），GET / PUT / PATCH / DELETE 一律 405；两边都只读，但**动词语义按各自的威胁模型定**
- [x] 框架 / CharApp / 商城三套测试全绿 —— 框架 **850** / CharApp **185** / 商城 **234**（`manage.py test app.minimall`，443.8s，**用户手动跑的**；下面那个 59 是它里面 `test_bff` 一份）

## 备注

- **想与 11 并行的话**：先做**框架历史端点那一半**（它不依赖任何 L2 前序片），去字段化等 12 的 17 个工具清单定稿再做 —— 否则标签表要维护两遍。
- **代价要说清**（ADR-0003 已记）：关掉了「在浏览器里看模型看到了什么」这个演示窗。它没有丢，只是**搬到了 L3 的轨迹落库** —— 那才是正确的排查入口（不受「谁能看到浏览器」约束，也不要求脱敏）。
- **脱敏必须在业务侧**：框架不该知道「什么算敏感」（那是业务知识）。这条与 `RunContext.payload` 同源 —— 框架只透传，不解释。
- **BFF 的 `relay()` 一行不改**：它是字节搬运，脱敏在更上游完成了。这是这条分层决定的主要收益 —— 如果要动 `relay()`，说明分层选错了。
- **历史端点只读，且不做「从第 N 号接着推」**（框架的 SSE 层明确不支持续推，见 `CharAgent/server/sse.py`）。刷新后是「重新拉一份完整历史，再开始新的一轮」，不是续流。
- **不做**：`reasoning` 的改写（ADR-0003 说了理由，归 L4 的 prompt 调优）、历史的分页（会话历史天然短）、历史的消息级删除。

---

## 实际开发情况 2026-09-22

两半都落地了：**工具事件去字段化**（CharApp 侧包装 sink + 17 条中文短语）与**会话历史**（框架只读端点 → BFF 转发 → 页面加载时恢复）。测试：框架 **850**（835 → +15）/ CharApp **185**（165 → +20）/ 商城 `test_bff` **59**（43 → +16）；**商城全量 234 全绿**（`manage.py test app.minimall`, 443.8s, 用户手动跑的）。`ruff check` 与 `format --check` 全仓干净。

### 拍板的开放项

| 事项 | 决定 | 理由 |
|------|------|------|
| **脱敏落在哪一层** | **服务入口**（`server.py` 的 `MinimallSessions.provide`），不在共用装配 | 见下一节 |
| **`status` 保留** | 保留 | ADR-0003 的表没列它；它不是用户数据，而页面靠它决定工具行红不红。**已把这一行补进 ADR 的表**（顺带补了一段「这条保证的范围」） |
| **白名单式脱敏** | 删掉一切不在保留表里的键 | 框架以后往载荷里加字段，默认进不了浏览器；要放行得回来改表 |
| **历史端点过滤角色** | 只回 user / assistant 的 `content`，逐条新建两个键 | 工具消息里就是工具返回正文 —— 不过滤的话，「刷新页面」成了一条绕过脱敏的路。ticket 没写这一条，但不做就是自相矛盾 |
| **没聊过 = 200 + 空列表** | 不是 404 | 第一次打开页面就是这样；404 会让调用方为一个**正常**情形写分支，而那条分支与「对话真的不存在」分不开 |
| **会话编号存 `sessionStorage`** | 是 | 按标签页隔离、刷新又还在 —— 正好是「刷新还在 / 新标签页是新的一段」这两条语义 |
| **BFF 读历史用 POST**（用户复核时定的，见下） | POST + 请求体 + CSRF；**下游那一跳仍是 GET** | 会话编号是私密数据，不该挂在 URL 上（访问日志 / 浏览器历史 / Referer 都会跟着它走），而 GET + cookie 还跨站可触发。下游是同机同信任域、地址里不带参数、要内部令牌才进得来 —— 那边的 GET 不成立 |
| **看过的两个小重复没动** | 不动 | `HISTORY_TIMEOUT` 与 `CANCEL_TIMEOUT` 字面相同（两条都是「对面立刻答」的短链路，值相同是巧合而非约束）；`forward_history(user_id, conversation_id)` 与 `forward_cancel(user_id, Cancellation)` 的参数形状不同。都够不上仓库那条 DRY 线（重复 ≥ 3 次），硬凑只会多一个空壳类型 |

### 「在 `service.py` 的装配处 —— 只有一处，CLI 与 server 都要过」这半句没照做

ticket 同一条任务里还写着「框架与 Django BFF 一行不改」，这两句**不能同时成立**：CLI 的事件出口是框架的 `EventPrinter`，它按框架的载荷契约渲染 —— `_line_tool_call` 打 `name(args)`、`_line_tool_result` 打 `name ok (耗时): summary`。载荷一旦脱敏，CLI 会打出 `add_to_cart( (畸形 JSON))` 与 `add_to_cart ok (1ms): `（`format_arguments("")` 走 JSONDecodeError 分支）。两全的唯一出路是去改框架那个渲染器。

按 `service.py` 自己的放置标准（「换一个入口还要不要这段」）：脱敏的威胁模型是**谁能看到浏览器**（ADR-0003 的原话），命令行是开发者自己的终端、本来就在同一个进程里，泄漏面为零 —— 这一段是**入口特有**的。ADR-0003 引的也正是框架把 sink 交给业务的那一处（`SessionProvider.provide`）。

**代价说清**：脱敏现在只挂在**服务入口**这一个点上，没有测试或类型在提醒「第三个入口也得包」。将来加入口时，`CharApp/minimall/server.py` 那个方法的 docstring 与 `redaction.py` 的模块说明是唯一的线索 —— 想更牢的话，可以在 `service.session_for` 上加一个必填的 `redact: bool`（把选择摆到台面上，而不是散在文档里）。

### 碰过的文件

| 文件 | 改动 |
|------|------|
| `CharApp/minimall/redaction.py` | **新增**：17 条中文短语表 + 兜底 + `redact()`（白名单原地改写）+ `redacting_sink()` |
| `CharApp/minimall/server.py` | `MinimallSessions.provide` 包一层 sink；docstring 补脱敏那一段 |
| `CharAgent/server/history.py` | **新增**：`conversation_of()`（wire 历史 → 展示用对话）+ 三个 wire 名 |
| `CharAgent/server/app.py` | 加 `GET /history`（同一个 ContextProvider 认身份）+ 模块 docstring |
| `CharAgent/server/__init__.py` | 门面导出 `HISTORY_PATH` / `MESSAGES_FIELD` / `THREAD_ID_FIELD`；结构总览补一行 |
| `app/minimall/views_bff.py` | `AgentHistoryView`（**POST** + 请求体 + CSRF）+ `forward_history()`（下游仍 GET）+ `conversation_id_from()`（三处校验收成一处） |
| `app/minimall/urls_bff.py` | 加 `history/` 路由 |
| `templates/minimall/agent.html` | 工具行改读 `label`；会话编号进 `sessionStorage`；加载时拉历史并复用同一套 DOM 渲染 |
| 测试 | `CharApp/tests/test_redaction.py`（新，18）/ `CharApp/tests/test_server.py`（+2）/ `CharAgent/tests/test_server_history.py`（新，15）/ `app/minimall/tests/test_bff.py`（+16） |
| `CharApp/docs/adr/0003` | 表里补 `status` 一行 + 「这条保证的范围」一段 |

### 真机核过一遍（起新 Django + 真商城 + 真模型）

买家登录 → `/minimall/agent/` → 问「我余额还有多少」→ 看 devtools 的 SSE 帧 → 刷新 → 开第二个标签页。四条结论：

1. **帧的形状**：`tool_call` = `{tool_call_id, tool_name, status, turn, label}`，`tool_result` = `{..., duration_ms, label}` —— `arguments` / `summary` 都不见了，`tool_name` 留着，两句 `label` 是「正在看账户和余额」/「账户信息拿到了」。
2. **刷新之后对话还在**：历史接口回的是 `{"thread_id": "minimall:24:5a65052d-…", "messages": [{user}, {assistant}]}` —— 没有 system、没有 tool 消息；余额只出现在模型自己那句话里。
3. **两个标签页各聊各的**：新标签页拿到另一个会话编号、空历史、提示语还在。
4. **只看得到该看的**：整条链路里没有一处把工具返回正文送到浏览器。

（后来改成 POST 之后，上面第 2 与第 4 条**又跑了一遍**：`POST /minimall/agent/history/`，请求体 `{"conversation_id": "8fe23830-…"}`，**地址栏里一个查询串都没有**，刷新照样恢复。）

### 用户复核时提的一条（GET → POST，2026-09-22）

原稿把 BFF 那条读历史做成了 `GET /minimall/agent/history/?conversation_id=…`，理由是「读操作本来就该 GET，而且它没有副作用」。用户否掉了：**不能让会话编号出现在 URL 上** —— 它一进地址栏就同时进了访问日志 / 浏览器历史 / Referer，而 GET + cookie 还是**跨站可触发**的（`<img src="…/history/?conversation_id=…">` 一行就能让别人的浏览器替他发这条请求）。改成 POST + 请求体 + CSRF 之后，三条路（提问 / 取消 / 读历史）形状一致，页面那边一套写法。

**下游那一跳仍是 GET**（`forward_history` → CharApp `/history`）：它是同机同信任域的内部跳，地址里不带参数（会话编号走 `X-Conversation-Id` 头），而且要内部令牌才进得来 —— 上面那些泄漏面一条都不成立，那里本来就是一次纯读。

**代价照记**：读操作用 POST 违反 HTTP 语义。这里认了 —— 这一层面向的是**浏览器**，那一边的威胁模型比动词的语义更重要。（ADR-0002 当初也是这么把 chat 从 GET 改成 POST 的，这条与它同源。）

### 真机抓到的那条（写下来，因为单元测试看不见它）

第一遍**刷新之后历史没回来**，页面上却一切正常。原因：`const STORAGE_KEY` 声明在 `loadConversationId()` 的**调用之后**，取值撞上暂时性死区抛 `ReferenceError` —— 而它正好落在那个函数自己的 `try / catch` 里，被当成「这个浏览器存不了」静静吞掉。表现就是「存不下」，页面照常能用、控制台一片干净。

Django 那三条断言源码字符串的用例全绿（`window.sessionStorage.getItem(STORAGE_KEY)` 确实在源码里），**只有真在浏览器里刷一下才看得见**。改法是把键改成函数内声明（不是「记得把声明往上挪」—— 那样下次重构又会踩），并在旁边写明这段历史。

### 代码审查改了什么（两轴：Standards + Spec）

**真发现的四条**：

1. **文档夸了口**（两轴都提）：「敏感数据不出本进程」（`redaction.py` 开头）与「订单号 / 地址 / 余额因此不出本进程」（`server.py`）都是**假**的 —— `reasoning` 按 ADR 不动，模型复述订单号时它就在帧里。改成「工具的参数原文与返回正文不出本进程」，并在两处写明范围。
2. **一条用例名不副实**（两轴都提）：`test_no_phrase_leaks_an_english_name` 用 `line.isascii() is False` 判，`正在处理 add_to_cart ✓` 照样绿 —— 兜底漏英文名那条其实没钉住。改成查拉丁字母。
3. **ADR 与代码对不上**（Spec 轴）：`KEPT_KEYS` 里留了 `status`，而 ADR-0003 的表没列它（ticket 还说那张表「逐字列了哪些改哪些不改」）。选择是**把 ADR 补齐**而不是把代码削回去 —— 页面确实需要它。
4. **BFF 只读那条只测了 POST**（Spec 轴）：补上 PUT / PATCH / DELETE（同一个出口，但「只读」是验收里明写的一条）。

**看过但有意不改的**：`HISTORY_TIMEOUT` 与 `CANCEL_TIMEOUT` 字面相同（见上表）；`forward_history` 与 `forward_cancel` 的形状重复（两个调用点，够不上仓库的 DRY 线）；前端那句 `data.status === 'ok' ? …` 与 `redaction.py` 的 done/failed 是一处小重复 —— 它是**兜底**（服务端没给 label 时），删掉只会让那行变成空白。

### 留给下一片

- **L3 落轨迹时注意**：脱敏是**原地**改事件对象的，而 `EventBus.emit` 的顺序是 sink 先、`ON_EVENT` hook 后 —— 挂在 `ON_EVENT` 上的轨迹记录拿到的是**脱敏后**的载荷。「看模型当时看到了什么」要走 wire 历史 / 快照那条路（ADR-0003 说的也是那条），别指望事件钩子。
- **验收第一条的措辞**：ticket 原话是「SSE 帧的 data 搜不到任何后端字段」，而 ADR-0003 明确不遮 `reasoning` / `final` —— 模型复述一个刚查到的订单号，那个号就在帧里。这条验收按字面**不可达成**；实际判据是「工具事件里搜不到」，已改在验收那一行。
- **历史只到进程内存为止**：服务重启之后，同一段对话的历史拉回来是空的（会话登记表在进程里）。快照里其实还留着，但那要 L3 的轨迹接口去读 —— 现在不是 bug，是边界。
- **`session_for` 的 `redact` 开关**（见上「代价说清」那段）：想让第二个入口不再靠人记得，就把它做成必填参数。
