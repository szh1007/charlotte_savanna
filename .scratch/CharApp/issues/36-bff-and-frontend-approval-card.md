# 36 · 业务侧：BFF 转发 + 前端确认卡（含刷新恢复与未决期间禁用输入）

**Status:** done

**Type:** task

**Blocked by:** issue 35（支付端点与工具先存在）

**上游:** `CharApp/docs/PLAN.md` §5 的 L3b 段第 4 条；**ADR-0015**（密码通路的最后一段）；`CharApp/CONTEXT.md` 的「恢复」「确认人」「一次性载荷」三个词条

## 一句话

让用户在**客服页面上**看见"这一步需要你确认"、**本人输入支付密码**、然后看着运行接着跑完 —— 而密码**不进对话历史、不进日志、不留在这一页的任何地方**。

## 现状（2026-09-24 核实）

| 零件 | 状态 |
|------|------|
| BFF 的 SSE 透传骨架 | ✅ `open_upstream`（`app/minimall/views_bff.py:787-855`）+ `relay`（`:858-895`，逐帧、记 `last_seq`、见终局置 `seen_terminal`、断流补 `error_frame`） |
| BFF 的转发样板 | ✅ `AgentChatView.post`（`:1142-1176`）与 `forward_cancel`（`:1074`）；五个 `forward_*` 函数（`:989-1074`）；`_service_headers`（`:722-733`）拼三个内部头 |
| 身份 | ✅ 每个视图 `LoginRequiredMixin` + **只取 `request.user.pk`**（`:1152-1154` 注释明说"请求体里就算带了 user_id 也不看"） |
| 前端的 7 类事件渲染 | ✅ `RENDERERS`（`templates/minimall/agent.html:508-548`），未知名静默忽略（`:552`） |
| 前端的读流与发问 | ✅ `ask(question)`（`:594-650`）：fetch `chat/`（`:607`）→ 从响应头取 `X-Run-Id`（`:630`）→ 读流渲染。输入框 `#ask-input`（`:198`）、发送 `#ask-send`（`:200`）、停止 `#ask-stop`（`:199`） |
| 支付密码输入 UI | ✅ **样式可参考** `templates/minimall/order_detail.html:126-148`（Alpine 弹窗，`:133` 是 `<input id="pay-hidden" type="password" inputmode="numeric" maxlength="6">`）—— **但搬不过来**，`agent.html` 是**原生 JS 无框架**（单段 module，`:230-1251`） |
| **确认卡 / 审批 UI** | ❌ **零**。全文只有 `:1024` 一处原生 `window.confirm('删除这段对话?')`（会话删除），与审批无关 |
| **`resume` 的转发** | ❌ 不存在 |

## 一、BFF：`POST /minimall/agent/resume/`

照 `AgentChatView`（`:1142-1176`）的形状再造一个：

- **认证**：`LoginRequiredMixin` + 取 `request.user.pk`（**不看** body 里的任何身份字段）
- **body**：`{conversation_id, run_id, decision, password?}`
  - `decision` 只认 `"approve"` / `"reject"` 两个值，其余 → 400
  - `password` **只在 `approve` 且 `needs` 含 `payment_password` 时出现**
- **转发**：`POST {CHARAPP_SERVER_URL}/runs/{run_id}/resume`，body 原样带上 `decision` 与 `data`（`data` 里的键就是 `needs` 里声明的那些）
- **回带 `X-Run-Id`**（与 `chat/` 一致，`:1171-1172`）—— 前端要继续用同一个 run 编号读流
- **响应**：`StreamingHttpResponse`，逐帧透传（复用 `open_upstream` + `relay`）

**两处必须同步的登记**：

| 处 | 位置 | 改什么 |
|---|---|---|
| BFF 的终局集 | `views_bff.py:196` 的 `TERMINAL_EVENTS = {"final","error"}` | 加 `approval_required`（issue 34 已把它定为终局事件） |
| 路由 | `app/minimall/urls_bff.py:36-63` | 加一条 `resume/`（前缀 `/minimall/agent/`，`app_name="minimall_bff"`） |

**密码绝不进日志** —— 与 issue 29 同一条线。这一条要有验收项（构造一次代付，两侧日志里搜不到密码原文）。

## 二、前端：确认卡

### 渲染

`RENDERERS`（`:508-548`）加一项 `approval_required`。载荷（issue 34 定的）：`tool_call_id` / `tool_name` / `prompt` / `needs`。

卡片在**会话流里原位插入**（与 `tool_call` 那一行同一个位置逻辑，`:516` 的 `turn.calls` 那套）：

```
┌─────────────────────────────────────────┐
│ ⚠ 这一单要付款了，需要你输一次支付密码     │   ← prompt（业务给的话术，前端原样显示）
│                                          │
│  支付密码  [••••••]                      │   ← needs 含 payment_password 时才渲染
│                                          │
│         [ 确认 ]      [ 取消 ]            │
└─────────────────────────────────────────┘
```

- 输入框照 `order_detail.html:133` 的配方：`type="password"` + `inputmode="numeric"` + `maxlength="6"`；确认按钮 `disabled` 直到满 6 位（`:140-145` 的做法）
- **纯「是/否」的确认**（下单前确认，issue 37）：`needs` 为空 → **不渲染输入框**，只有两个按钮
- **手写 DOM**，不引框架 —— `agent.html` 是零构建的原生模块，这条不能破

### 提交

`确认` → `POST resume/`（带 `decision` 与密码）→ **复用 `ask()` 的读流逻辑**（`:594-650`）把返回的 SSE 流渲染出来。取消 → 同一个端点，`decision="reject"`，无密码。

**提交后立刻清空密码**：把输入框的值置空、卡片换成"已提交"的静态态。密码不留在这个页面的 DOM 里。

### 刷新恢复

**这是最容易被漏掉的一条**：前端读的是**记录（Transcript）**，而挂起态在 `charagent_tool_calls` 那一行 —— 两者不在同一条读取路径上。**不补这一条，刷新页面后确认卡就消失了，用户永远没法完成那次代付。**

- 框架侧 `GET /history`（`CharAgent/server/app.py:317` 一带）的响应里要带一个「本会话是否有未决挂起」的字段（含 `tool_call_id` / `tool_name` / `prompt` / `needs`，够前端**重建**那张卡）
- `loadHistory`（`agent.html:679-724`）拿到它就重建卡片
- **不新建表、不新建端点**（PLAN 已定）：判据就是 `charagent_tool_calls` 里 `status = needs_approval AND approved_at IS NULL` 且属于本会话

### 未决期间禁用输入

有未决挂起时：`#ask-input` 与 `#ask-send` **disabled**（`#ask-stop` 照旧可用）。

**但前端禁用只是体验，不是闸门** —— 后端也必须拒（issue 34 的 `ThreadSuspendedError` → 409 翻成用户看得懂的话）。**两条都要有**：只做前端等于没有。

## 交付物

| # | 内容 |
|---|------|
| 1 | BFF `POST /minimall/agent/resume/` + 路由 + `TERMINAL_EVENTS` 加 `approval_required` |
| 2 | `GET /history` 响应带未决挂起信息（**框架侧改动**，见"刷新恢复"） |
| 3 | 前端：`approval_required` 渲染器 + 确认卡（含密码输入 / 纯是非两态）+ 提交读流 + 提交后清密码 |
| 4 | 前端：`loadHistory` 重建卡片 |
| 5 | 前端 + 后端：未决期间拒绝新提问（禁用 + 409 两条） |
| 6 | 用例：BFF 的 resume 转发（含 `decision` 值校验、身份只取 session）；前端行为由真机验收（本项目的既有做法） |

## 验收

- [x] 真机：说「帮我付了这单」→ **页面弹出确认卡**（不是模型在对话里问一句）→ 输密码 → 付款成功，订单变 `paid`
- [x] **刷新页面后确认卡还在**（未决期间刷新）
- [x] 未决期间：输入框禁用；绕过前端直接 POST `chat/` → 被拒
- [x] 取消（`reject`）之后：模型收到一条**工具结果**（拒绝原因）并继续答，卡片消失
- [x] **提交后页面上搜不到密码原文**（DOM 里、以及 devtools 的网络面板里——签名之后请求体里也没有）
- [x] **两侧日志里搜不到密码原文**（与 issue 29 呼应）
- [x] 纯是非形态（issue 37 的确认）也能渲染，且没有输入框

## 实施记录（2026-09-26）

### 交付物六条

| # | 内容 | 落在哪 |
|---|------|--------|
| 1 | BFF `POST /minimall/agent/resume/` + 路由 + 终局集 | `views_bff.py`：`Resume` / `resume_from_request` / `pending_approval` / `declared_needs` / `one_shot_payload` / `open_resume` / `_sse_response` / `AgentResumeView`；`urls_bff.py` 加一条 `resume/` |
| 2 | `GET /history` 带未决挂起 | **issue 34 已经交付**（`CharAgent/server/history.py` 的 `pending_approval`，五样齐全）—— 本片框架侧**一个字没改**，前端直接吃那一块。用例 `test_the_pending_fields_match_the_framework` 盯着字段名别漂 |
| 3 | 前端：渲染器 + 确认卡（两态）+ 提交读流 + 提交后清密码 | `agent.html`：`RENDERERS.approval_required` · `showApprovalCard` / `buildApprovalCard` / `decide` / `readDecidedRun` |
| 4 | 前端：`loadHistory` 重建卡片 | `loadHistoryBody` 末尾一句 `showApprovalCard(payload.pending_approval, false)` |
| 5 | 前端 + 后端：未决期间拒绝新提问 | 前端 `syncComposer` 加第三个条件（未决即锁）；后端是框架那条 `ThreadSuspendedError`（issue 34）**加上本片补的用户话术**（`ERROR_COPY["thread_suspended"]`） |
| 6 | 用例 | **25 条新增**（业务侧）+ 2 条既有跟着改（事件七类→八类、CSRF 发送点计数）+ **1 条框架侧**（`data` 只增不覆盖，见「收尾后补修」） |

### 四处改判（都写进了代码注释）

1. **BFF 的请求体是 `{conversation_id, decision, data}`** —— 票据写的是
   `{conversation_id, run_id, decision, password?}`，两个字段都换了：

   - **`run_id` 不要**：框架里有**两个**运行编号，而页面上那个（`X-Run-Id` / 事件
     载荷里的）是**这一次 HTTP 请求**的进程内编号，恢复端点要的是**记录层那一行**
     （issue 34 的 docstring 自己写着"拿错了会 404"）。直播那一路手上只有前者 ——
     带上一个"看着像"的编号过去，换来的是一次 404：用户点自己的确认，却被告知
     "已经处理过了"，而他什么都没做过。于是"哪一次运行"由**唯一知道答案的那一方**
     回答：BFF 现问一句 `/history`（判据与框架那道闸门逐字相同），代价是用户点确认
     时多一次上游读。
   - **`password` 改成 `data`**：`data` 是框架那边同一个字段名（`read_approval` 认
     的就是它），本层因此**不必认识凭据的名字** —— 键名只有前端（它按 `needs` 渲染
     那个框）与业务装配知道。少一处写死凭据名的地方。
     **但原样转发不行，得按 `needs` 筛**（见「两轴复核后的修补」第一条：那不是一份
     单纯的凭据，框架会把它并进运行上下文，而身份也在那份载荷里）。

2. **`thread_suspended` 到浏览器是 502（码与话术原样带上），不是 409**：与隔壁
   `thread_busy` 同一条分法（`_open_stream` 那张表：上游非 200 一律塌成 502 +
   保留它的码）。页面消费的是**码 + 用户话术**那两样，为这一条去改状态码语义会顺带
   改掉 `thread_busy` 的既有行为 —— 那是另一件事，不在本片。

3. **卡片插在"当前这一轮的过程区"**（票据那句"与 `tool_call` 那一行同一个位置
   逻辑"）：直播那一路后面还会有事件（恢复那段的 `tool_call` / `tool_result` /
   `final`），插在过程区末尾 = 顺序就是发生顺序；刷新重建那一路没有"当前这一轮"，
   就挂在会话流末尾（记录里最后一轮的过程区）。**同一时刻只有一张卡**：新卡上来时
   把旧的那张摘掉（它是运行状态的渲染，不是聊天记录 —— 刷新之后本来也不在）。

4. **恢复那一段的答复进的是"当前这一轮"**：刷新之后直接点卡时页面上没有"当前这一
   轮"，`startTurn('')` 现起一块空轮 —— 为此让 `startTurn` 支持**空问句**（不摆那个
   气泡：这一步不是新问的问题，是接着上一次问答往下跑）。

### 真机（2026-09-26：真 Django + 真 Postgres + 真模型 + 真商城）

两个进程各起一个：Django `runserver 127.0.0.1:8090`（**不是 8000**，见残留 1）+
CharApp 服务（端口 1007，`CHARAPP_BASE_URL` 覆盖到 8090）。浏览器走 Playwright，
买家 `savanna`（`user_id=10`），四个场景：

| # | 验收项 | 实际 |
|---|--------|------|
| 1 | 说「帮我把订单 …付了吧」→ 卡片 → 输密码 → 订单变 `paid` | 事件序 `思路 → 操作 正在给这一单付款 → ⚠ 卡片`（**模型没有在对话里问一句**）→ 输满六位「确认」可点 → 流接着跑：`操作 这一单付好了` + 答复"付好了 —— 这一单 99.00 元, 订单状态已经是已付款…余额还剩 19406.00"；库里 `pay_my_order [succeeded]` 批准=10；订单 `202609260053160000100050` → `paid`；余额 19505.00 → 19406.00（恰是订单金额） |
| 2 | 未决期间刷新 → 卡片还在 | `location.reload()` 之后卡片重新出现（来源只能是 `/history` 的 `pending_approval`），输入区仍锁着 |
| 3 | 未决期间：输入框禁用 + 绕过前端直接 POST `chat/` 被拒 | 无障碍快照里 `textbox [disabled]` / `发送 [disabled]`；直接 `fetch('/minimall/agent/chat/')` → **502 + `thread_suspended`** + "上一步还等你确认呢, 先把那张卡片处理掉再问下一句吧."（Django 日志同一拍记了 `客服服务拒绝了这次转发: HTTP 409 (thread_suspended)`） |
| 4 | 取消之后：模型收到工具结果并继续答，卡片消失 | 点「取消」那一瞬卡片还在（要等提交成功），跑完 `卡片还在不在: false`；答复"这一单没有付款 —— 你在确认卡上点了取消, 所以没有扣钱, 订单还是待付款状态"；库里那条 `[failed]` 批准=None，模型拿到的是"用户没有批准这次操作: 这一步没有执行, 也不要重试它" |
| 5 | 提交后页面上搜不到密码原文 | 提交那一刻：`passwordInDom: false`、密码框被拆掉、卡片变"已提交, 正在继续…"。**网络那一半**另走一遍（把页面的 `fetch` 挂上钩子，故意输错六位，不真付款）：4 个请求里**只有一个**带那个值 —— `/minimall/agent/resume/`（设计通路本身，ADR-0015），它之后只有 `/minimall/agent/conversations/`（不带）。顺带验了输错密码那条路：答复是"密码是一次性的, 这次没通过…重新跟我说一声, 我再给你弹一张新的卡"（**没有重试**） |
| 6 | 两侧日志里搜不到密码原文 | BFF 那份 43 行日志 **0 处**（正对照：resume 那条访问记录与 `thread_suspended` 都在，说明日志真的记了这件事）；服务进程那份几乎不打 INFO（访问日志按 ADR-0019 关着），于是另查框架的记录表：17 次运行里，`payment_password` 这个名字在**消息 / 调用参数 / 调用结果 / 快照**四处一处都没有（正对照：订单号四张表都搜得到） |
| 7 | 纯是非形态也能渲染，且没有输入框 | 用临时护栏替身模拟 issue 37 那条规则（`place_order` + `requires_approval(needs=())`）：卡片"⚠ 确认要下这一单吗?"只有**确认 / 取消**两颗按钮、**零个输入框**、确认一开始就可点（不必凑六位）→ 点确认 → 订单真的下成（`202609260222000000102317`） |

**四条伪证**（一起改坏，跑一遍，看用例红不红）：终局集去掉 `approval_required` →
`test_a_suspended_run_is_a_terminal_event_not_an_interruption` 红 · 不校验 `decision`
→ `test_an_unknown_decision_never_reaches_the_service` 六个子用例全红 · 没有未决挂起
时报 502 → `test_nothing_pending_is_a_gone_card_not_a_wiring_failure` 红 · 前端输入区
不看未决挂起 → `test_a_pending_approval_locks_the_composer` 红。**9 条红，除这四条之外
没有别的用例变红**；还原后重跑 59 条全绿。

### 跑过的用例（收尾那一遍）

| 命令 | 结果 |
|------|------|
| `manage.py test app.minimall --noinput` | **326 passed**（本片新增 25 条全在里面） |
| `CharApp` · `pytest -q` | 228 passed |
| `CharAgent` · `pytest -q` | **1284 passed, 130 deselected**（本片改动之后；那 130 条是 `pg_db` 标记的真库用例） |
| `ruff check .` + `ruff format --check .` | All checks passed · 692 files already formatted |

### 收尾后补修（用户 2026-09-26 看完残留清单之后的四条决定）

| 决定 | 落在哪 |
|------|--------|
| **框架 `data` 只增不覆盖** | `CharAgent/server/app.py` 的恢复端点：`payload={**data, **context.payload}`（原本是 `{**payload, **data}` —— 客户端的 `data` 能顶掉运行上下文里已有的键，而身份就在里面） |
| **卡片过期直接作废，不许锁着输入区** | `agent.html` 新增 `ui.void`：摘卡 + 清 `pendingApproval` + `syncComposer()`（放开），只在会话流里留一句说明；`decide` 的 404 那一支改走它 |
| **卡片必须校验是同一张** | 前端把 `tool_call_id` 一起送（`ui.toolCallId`，卡片那一块里就有）；BFF 新增 `Resume.tool_call_id`（必填，缺了 400）+ `Pending.tool_call_id`，对不上就按"卡过期"回 404（`views_bff.py` 的 `AgentResumeView`） |
| 清开发库那几笔 | 见下（导出留档，然后删） |

**框架那条（残留 6 的处置）**：`{**data, **context.payload}` —— 一次性的东西只该
**补上缺的那些**，已有的键（谁 / 哪一段会话）一律以本次运行为准。用例
`test_the_one_shot_payload_can_only_add_never_override`（假业务把身份放进载荷，
恢复时带一个 `user_id` 想覆盖）—— **伪证**：把顺序改回 `{**payload, **data}` 它当场红。
写这条时还撞了一次框架自己的边界用例（`test_the_framework_never_mentions_the_business`）：
我第一版注释里写了业务路径（`CharApp/minimall/...`）与"买家"，被它抓出来 —— 改掉才绿
（框架源码里不许出现业务词）。

**C1/C2 的真机补验**（2026-09-26 11:52，两个服务重新起来，Django 这次在用户拿回的
**8000** 上）：起一张活的卡（`askDisabled: true`）→ ① 拿页面自己的 `fetch` 送一个
**别的** `tool_call_id` → **404**（卡与输入区都不动，挂起还在）→ ② 用内部令牌从服务端
`POST /runs/{id}/cancel` 把那条挂起撤掉（页面不知情，卡还亮着）→ ③ 页面上输密码点「确认」
→ **404** → 页面当场**作废**：`卡片还在不在: false` · `输入区还锁着吗: false` · 流里
多一句"这一次确认已经过期了, 这张卡片作废 (刷新页面能看到最新状态)." · 密码不在 DOM 里。
日志两边各一条 `Not Found: /minimall/agent/resume/`（11:53:18 是①、11:54:14 是③）。

### 清开发库（用户发话之后，2026-09-26 11:38）

按老规矩**先导出到 Temp 再删**（脚本 `Temp/hitl36_cleanup.py`，导出落
`Temp/hitl36_清库导出/`）：

| 动的东西 | 结果 |
|---|---|
| 订单 `202609260053160000100050`（已付款，**ticket 35 那次跑出来的**、被本片真机付掉）· `202609260222000000102317` · `202609260241020000103960` | 三笔连同明细删掉（导出留档：单号 / 状态 / 金额 / 明细） |
| 商品库存（`place_order` 扣过 3 件） | 还回 3 件（978 → 981） |
| 余额（那笔付款扣的 99.00） | 设回 **19505.00**（本片开工时的值） |
| 三段会话（`60f96b6b-…` / `5c99b3a5-…` / `1000369b-…`）+ 补验那一段（`be6bcc20-…`） | 删掉（threads → runs / messages / checkpoints / tool_calls 级联；恢复留下的幂等键一并清） |
| 用户自己那两段（13:24 / 13:28）· ticket 35 那五段（`hitl35-…`）与它们的订单 · 别人的数据 | **一律没碰** |

> 那笔已付款的订单其实是 ticket 35 建的（09-25 16:53，编号 `…100050`），本片真机把它
> 付掉了 —— 所以按"本片造成的那一下"一并清了。导出里只有单号 / 状态 / 金额 / 明细
> （**没有**地址快照与付款时刻），要原样放回去做不到；需要的话重下一笔即可。

### 两轴复核（`/code-review`）后的修补

**规格轴逮到一个真漏洞 —— 这一片唯一一处安全修补，也是收尾改动最大的一处**：

- **漏洞**：本层原先把浏览器给的 `data` **原样**转发。而框架把 `data` **并进**运行
  上下文，而且是 `{**payload, **data}` —— `data` 在后，**覆盖得掉已有的键**
  （`CharAgent/server/app.py` 的恢复端点），而那份载荷里装着这一趟运行的**身份**：
  业务侧取买家 ID 正是从载荷里读的（`CharApp/minimall/provider.py` 的 `buyer_id`
  → `payload["user_id"]`）。于是：一个买家在自己那次挂起上带一个
  `data={"user_id": 别人的}`，恢复那一段就以别人的身份查订单与余额，答复还流回他
  自己页面上 —— 而**本层是挡住这条路唯一的门**（浏览器够不着助手服务）。
  它正是票据原来那句「`password` **只在 `approve` 且 `needs` 含
  `payment_password` 时出现**」的落点，改判 1 的第二半把这道闸丢了。
- **修法**：`pending_approval` 顺带把挂起声明的 `needs` 拿回来；新增
  `one_shot_payload(resume, needs)` —— **白名单**（只放行挂起声明缺的那几个键，
  别的默认进不来）；拒绝那一路一个键都不带（框架只在拒绝时读一个可选的 `reason`，
  今天没有调用方给它）。
- **证据三处**：① 用例 `test_the_payload_is_trimmed_to_what_the_approval_declares`
  （请求体里塞 `user_id` 与 `tenant_id`，断言转过去的只有 `payment_password`）与
  `test_a_yes_or_no_approval_carries_nothing_at_all`；② 真机：拿页面自己的 `fetch`
  直接 POST `resume/` 并注入 `data={"user_id": 1}` → 恢复照常跑完，下成的订单
  `202609260241020000103960` 属主仍是 `user=10`（savanna）—— 而 `user_id=1` 这个
  买家**压根不存在**，没筛的话那一趟只可能以"买家不存在"失败、不会有订单；③ 框架那
  一侧的根因记进残留 6。
- **前端一个"点不动"的缺陷**（同一轴逮到）：纯是非的卡（没有密码框）提交失败一次之
  后，「确认」**永远是灰的** —— `refresh()` 只在 `if (box)` 里动那颗按钮，而失败复位
  走的正是它。修法：`ok.disabled = Boolean(box) && box.value.length !== PASSWORD_LENGTH`
  （一处判、两种形态共用一个出口）。真机验过：把 `resume/` 请求掐断一次（Playwright
  拦下），卡上出现"没能连上客服, 检查网络后再试一次."，而**确认仍可点**
  （`okDisabled: false`）。

**规范轴**（四条，都是"半截改名"与抄了一份）：① 三处注释跟着数量漂了 ——
`urls_bff.py` 的"四个转发端点"（→ 五个）· `views_bff.py` 模块头那段逐条走查（补上
第五条路）· `agent.html` 的"七类事件"两处（→ 八类）；② 提问与确认各抄了一份"这次
fetch 的契约检查"（三种失败判据逐字相同）→ 抽成 `postStream`，顺带收掉一处**已经
漂了的文案**（"再问一次" / "再试一次"）；③ `readDecidedRun` 抄了 `ask` 的四行前奏
→ 抽成 `beginRun`；④ `parts` 这个篮子（ok/no/box/reset/settle）改名 `ui`。
代价写在明处：页面源码用例里"发送点计数"从 6 降到 5（提问与确认合用一个），那条注释
跟着改成"五处发送点，一个都不许自己另起 `fetch` 忘带令牌"。

**不判**（记下来，免得下次再纠结）：`open_upstream` / `open_resume` 现在只是
`_open_stream` 的两次调用（Middle Man 的轻量嫌疑）—— 留着，它们是两条路各自的名字
与文档，机制只在 `_open_stream` 一处 · `Resume.data: dict` 看着像 Primitive
Obsession，但 ADR-0015 与票据都明说这一层不解释载荷 · 状态码仍按改判 2（502）。

### 用例（新增 22 条）

| 文件 | 断的是什么 |
|------|-----------|
| `app/minimall/tests/test_bff.py` 的 `BffResumeTest`（16，新） | 结论与载荷转发到**库里那一行**的运行（含 `data` 真的带上去了）· **载荷按 `needs` 筛过**（塞 `user_id` / `tenant_id` 只留下声明缺的那个）· 纯是非的挂起什么都不带 · `data` 不是对象 → 400 · 取消不带任何载荷 · 身份只从 session 取 · **要恢复哪一次运行由 `/history` 说了算**（body 里塞一个别的编号也改不了）· `decision` 只认两个值（六个坏值各一遍，且不许惊动上游）· 会话编号不合法就不去读历史 · 没有未决挂起 → 404 `run_not_found` 且不打恢复端点 · 读挂起失败时上游正文不外泄 · 回复体不是 JSON → 502 · 带 `X-Run-Id` 与 SSE · **密码不进日志**（正对照：状态码在）· GET 一律 405 · **`pending_approval` 的字段名与框架同源** |
| 同文件 `BffFailureTest`（+2） | **挂起是终局事件**（流尾不再补一帧"回答中途断开了"）· 未决期间新提问的 409 翻成"先处理那张卡"（不是"联系不上"） |
| 同文件 `AgentPageTest`（+4） | 卡片两个来源（事件 / `/history`）+ `approval_required` 算终局 + 那句话由服务端给 + 形态由 `needs` 决定 · 密码框那套配方（`type=password` / 数字键盘 / 六位 / `autocomplete=off` / 满六位才可点 / 提交即清）· **页面不送运行编号** · 未决期间锁输入区（并指路服务端那条闸门） |

### 残留（都已记进代码注释或下一片）

> 用户 2026-09-26 把其中三条**当场改判**了（见上「收尾后补修」）：卡片过期改成
> **直接作废**（不再锁输入区）· 卡片**要校验是同一张**（`tool_call_id`）· 框架那条
> `data` 合并语义收成**只增不覆盖**。下面第 2、3、6 条是**改判前**的记录，留着看思路。

1. **本机 8000 绑不上**（**已解决**）：Windows 动态保留了 `7984-8083` 这一段
   （`netsh int ipv4 show excludedportrange protocol=tcp` 可查），而仓库 `.env` 的
   `CHARAPP_BASE_URL` 指的正是 `127.0.0.1:8000` —— 第一次跑就是栽在这个上（付款调用
   2 秒后以"工具执行时发生内部错误"收场，而商城访问日志里一条付款请求都没有）。
   用户当天把保留段排掉了，补验时 Django 起回 8000 ✓。**再撞上就这么办**：`net stop
   winnat` + `net start winnat`（管理员），或者把 Django 起在段外再给服务进程一个
   `CHARAPP_BASE_URL` 覆盖。这不是本片的代码问题。
2. **（改判前）卡片过期之后**：原先是"卡片留在原地 + 一句提示，输入区仍然锁着" ——
   用户改成**当场作废**，理由是一张按不动的卡摆在页面上只会让人以为"点了没反应"。
3. **（改判前）不校验卡片是不是同一张** —— 用户改成**必须校验**（`tool_call_id`）。
4. **prompt 里那句"不能替买家付款"还在 v2**（改写是 issue 37）：真机用的是
   `Temp/hitl35prompt` 那份 v3 草稿，加 `Temp/hitl36_service.py` 里那条**临时护栏**
   （`place_order` 要人点头）—— 37 落地时两样都删掉。
5. **真机在开发库里留下的数据**：**已按用户发话清掉**（见上「清开发库」那一节：
   导出留档 → 删订单 / 还库存 / 还余额 / 删会话）。
6. **（改判前）框架那条根因** —— 用户改成**只增不覆盖**，已实现并验过（见上）。

## 改了哪些文件

| 文件 | 改了什么 |
|------|---------|
| `app/minimall/views_bff.py` | 终局集加 `approval_required` · `RESUME_PATH` / `DECISION_FIELD` / `DATA_FIELD` / `PENDING_*` / `TOOL_CALL_ID_FIELD` 常量 · 六个新码的话术 · `Resume`（含 `tool_call_id` 必填）+ `resume_from_request` · `Pending` + `pending_approval` + `declared_tool_call_id` + `declared_needs` + `one_shot_payload` · 卡片身份对不上 → 404（`AgentResumeView`）· `open_upstream` 拆出 `_open_stream`（两条流式路共用）+ `open_resume` · `_sse_response` · 模块头补第五条路 |
| `app/minimall/urls_bff.py` | 加一条 `resume/`（`name="resume"`） |
| `templates/minimall/agent.html` | 卡片那一段 CSS · `pendingApproval` 状态 + `syncComposer` 第三个条件 · `approval_required` 渲染器 + 终局判定 · 卡片那套函数（建 / 挂 / 提交 / 读流 / **作废**）· `ask` 拆出 `postStream` + `consumeRun` + `beginRun`（提问与恢复共用）· `startTurn` 收空问句 · `loadHistoryBody` 重建卡片 · `clearChat` 放闸 |
| `app/minimall/tests/test_bff.py` | 25 条新增 + 2 条既有跟着改（见上） |
| `CharAgent/server/app.py` | 恢复端点的载荷合并改成**只增不覆盖**（`{**data, **context.payload}`, 收尾后按用户决定改的） |
| `CharAgent/tests/test_server_approval.py` | 1 条新增（`test_the_one_shot_payload_can_only_add_never_override`）+ 假业务的上下文载荷里补一个"已有的键"给它当靶子 |

**没动的**：`views_bff.py` 里那几条既有转发路（`_call_upstream` 一行没改）·
`agent.html` 的既有骨架（零构建、手写 DOM、`sessionStorage` 会话编号这些都没动）·
框架其余部分（本片只动了恢复端点那一处合并语义）。

## 备注

- **不做"记住密码"、不做任何便捷口子**（ADR-0015 已否）。密码是一次性的，输错就重来一遍。
- **确认卡不是聊天消息**：它不该被写进会话历史（它是 UI 对一次运行状态的渲染，而运行状态在 `charagent_tool_calls` 里）。刷新恢复走的是 `/history` 的那个字段，**不是**把它塞成一条 message。
- **`agent.html` 已经 1252 行**，本片会再加一段。它已经是单模块手写 DOM 的形状，**本片不重构它**（那是 L4 的前端迁移那件事，见 issue 25 的处置）。
