# 36 · 业务侧：BFF 转发 + 前端确认卡（含刷新恢复与未决期间禁用输入）

**Status:** todo

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

- [ ] 真机：说「帮我付了这单」→ **页面弹出确认卡**（不是模型在对话里问一句）→ 输密码 → 付款成功，订单变 `paid`
- [ ] **刷新页面后确认卡还在**（未决期间刷新）
- [ ] 未决期间：输入框禁用；绕过前端直接 POST `chat/` → 被拒
- [ ] 取消（`reject`）之后：模型收到一条**工具结果**（拒绝原因）并继续答，卡片消失
- [ ] **提交后页面上搜不到密码原文**（DOM 里、以及 devtools 的网络面板里——签名之后请求体里也没有）
- [ ] **两侧日志里搜不到密码原文**（与 issue 29 呼应）
- [ ] 纯是非形态（issue 37 的确认）也能渲染，且没有输入框

## 备注

- **不做"记住密码"、不做任何便捷口子**（ADR-0015 已否）。密码是一次性的，输错就重来一遍。
- **确认卡不是聊天消息**：它不该被写进会话历史（它是 UI 对一次运行状态的渲染，而运行状态在 `charagent_tool_calls` 里）。刷新恢复走的是 `/history` 的那个字段，**不是**把它塞成一条 message。
- **`agent.html` 已经 1252 行**，本片会再加一段。它已经是单模块手写 DOM 的形状，**本片不重构它**（那是 L4 的前端迁移那件事，见 issue 25 的处置）。
