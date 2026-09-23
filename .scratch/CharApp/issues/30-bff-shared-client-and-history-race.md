# 30 · 搭车两条：BFF 共享 client（~690ms）· 历史响应的竞态闸门

**Status:** todo

**Type:** task

**Blocked by:** 无（可与其他片并行）

**上游:** `CharApp/docs/PLAN.md` §5 的 L3a 段（"顺带搭两条车"）；两条都出自 **issue 25** 的「八、留给后面的」（真机反馈攒下的欠账）

## 为什么搭 L3a 的车

两条都与 L3a 要动的**同一份前端与同一个 BFF** 重合：L3b 要往 `agent.html` 加确认卡、往 `views_bff.py` 加转发，**先把它修干净再往上加东西**。而且第 1 条是真机上肉眼可感的那一下（"横幅先蹦一下"）。

---

## 一、BFF 每次转发新建 `httpx.Client`（真机 ~690 ms）

### 现状

`app/minimall/views_bff.py` 里 **3 处**创建，全是"每次请求新建、作用域结束即关"：

| 位置 | 用途 |
|------|------|
| `:815` `client = stack.enter_context(httpx.Client(timeout=UPSTREAM_TIMEOUT))` | 问答流（`open_upstream`） |
| `:957` `with httpx.Client(timeout=timeout) as client:` | `_call_upstream`，五条普通路共用 |
| `:1103` `with httpx.Client(timeout=CANCEL_TIMEOUT) as client:` | 取消 |

无连接池复用、无模块级单例。本文件**没有** `AsyncClient`。

### 但这里有一条**必须先回应**的既有理由

`open_upstream` 的 docstring（`:805-807`）明写着这是**有意的**：

> 「用每次请求一个 `httpx.Client`：Django 没有可靠的进程退出钩子」

**这条理由是成立过的**，不能装作没看见。它防的是"进程级连接池没人关"。所以本片要做的不是"改成单例"，是**在承认那条代价的前提下换一个更划算的**：

- **代价现在有数了**：本机 ~690 ms（issue 25 的真机观测），而且它落在**首字之前** —— 用户先看到一次空转
- **那条理由的前提是"多 worker"**：项目自己已经写明部署是**单进程**（`CharAgent/server/sessions.py:43-48` 的「单进程」一节，`SessionRegistry` 与 `RunRegistry` 都在进程内）。单进程下 `atexit` 是**可靠**的
- 于是：**模块级共享一个 client + `atexit.register(client.close)`**，并把"依赖单进程"这个前提写在 docstring 里 —— 与 `sessions.py` 那条前提**同源**，不是新引入的假设

**要核一件事**：`httpx.Client` 的连接池在**流式响应**（`iter_bytes`）与普通请求之间共用是否安全。`open_upstream` 的长连接与 `_call_upstream` 的短请求如果共用一个池，长连接会占住连接额度 —— 实施时按 `httpx.Limits` 配连接数，或**给流式那条单独留一个 client**。**两者都不违反"不再每次新建"**，选哪个看实施时测到的数。

### 验收

- [ ] 同一次会话里连续发三条短请求（改标题 / 置顶 / 搜索），首字节延迟相对基线明显下降（**记录实测数字**）
- [ ] 流式问答不受影响：`POST chat/` 的 SSE 透传行为一字不改（既有用例全绿）
- [ ] client 有明确的关闭路径（`atexit`），且 docstring 写明"依赖单进程"这个前提与它的出处

---

## 二、历史响应的竞态：`loadHistory` 缺一道闸门

### 现状

**闸门的样板在前端，不在 BFF**（BFF 侧没有任何序号闸门）：

| 函数 | 位置 | 防竞态方式 |
|------|------|-----------|
| `loadConversations` | `agent.html:1105-1151` | ✅ **递增 ticket + 回来比对**：`:1103` 定义 `listRequests = 0`，`:1110` 取号 `const ticket = ++listRequests`，`:1149` 回来时 `if (ticket !== listRequests) return` 丢弃旧响应。注释 `:1098-1102` 把竞态讲清楚了（"慢的那一份会盖掉快的那一份"） |
| `loadHistory` | `agent.html:679-724` | ⚠️ 只有一个布尔 `finished`（模块级，`:265`）：`:685` "用户已经在问了，别把历史盖到新一轮上"、`:717` "等这份 JSON 的工夫，用户可能已经问出去了" |

**差别**：ticket 能区分"两次加载谁更新"；布尔只能区分"聊没聊过"。于是**两次并发 `loadHistory` 之间无法判先后** —— 快速切会话时，先发的慢响应仍可能盖掉后发的快响应，**看到的是上一个会话的历史**。

`loadHistory` 的调用点只有三处（`:361` 重试按钮、`:1199` 切会话、`:1242` 首屏），**快速切会话正是触发路径**。

### 做法

**照 `listRequests` 再来一道 `historyRequests`** —— 修法现成，不需要新设计。注意 `:353-364` 那条 `historyFailed` 的判据（它 2026-09-23 因为不用 `finished` 而漏过一次）**要一起改成 ticket 口径**，否则两道判据会打架。

### 验收

- [ ] 快速连续切三次会话，最终显示的是**最后点的那一个**（旧响应被丢弃）
- [ ] 失败路径（`:353-364`）与新闸门口径一致，不会把"上一个会话的失败"报到当前会话头上
- [ ] 重试按钮与首屏两条路径不受影响

---

## 备注

- 两条都**不碰协议**：不改 BFF 与 CharApp 之间的请求/响应形状，不改 SSE 帧格式。L3b 要在这两处之上加东西（确认卡、`/resume` 转发），本片是**先把地基抹平**。
- 第 1 条**如果实测收益不明显**（比如 690 ms 主要来自别处），**可以只做第 2 条**并把实测数字记在本片 —— 别为了兑现规划去改一个不划算的东西。
