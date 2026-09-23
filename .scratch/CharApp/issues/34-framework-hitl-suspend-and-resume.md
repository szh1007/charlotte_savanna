# 34 · 框架侧：HITL 挂起-恢复（`Decision` 第三值 + `approval_required` 事件 + `POST /runs/{id}/resume`）

**Status:** todo

**Type:** task

**Blocked by:** issue 27（挂起态要落 `charagent_tool_calls`）· issue 32（恢复重放要被幂等键挡住）· issue 33（恢复段落要能记账）

**上游:** `CharAgent/docs/DESIGN.md` #25（HITL 高危审批：挂起 → 人工批准 → 从断点恢复，不从头重跑）；`CharApp/docs/PLAN.md` §5 的 L3b 段第 3 条；**ADR-0014**（挂起不建审批表）

## 一片一句话

让 `Decision` 能说「**这事我管不了，得问用户**」，并让整个运行**停在半路等人**，人给结论后**从存档点接着跑、只补做那一条调用**。

## 现状（2026-09-24 核实）：两头都在，中间断了

| 环节 | 状态 |
|------|------|
| 拦截点 | ✅ `HookPoint.BEFORE_TOOL_EXECUTE` 是六个点里唯一的**裁决类**（`hooks/utils/types.py:45`） |
| 裁决语义 | ⚠️ `Decision` **只有 `allow` / `reject`** 两值（`hooks/utils/types.py:50-94`）。它的 docstring 原话：「为什么**只**有放行 / 拒绝两值（P0/P1 不做「需要确认」）：那是另一种机制 —— 挂起 + 存档 + 用户确认 + 从存档点续跑……**#25 HITL 落地时再加**」 |
| 挂起数据结构 | ✅ `Suspension(reason, pending, approval_id)` 在 `checkpoint/utils/types.py:137-157`；挂在 `CheckpointState.suspension`（`:208`）；序列化读写齐（`checkpoint/serialization.py:460 / 616 / 646`） |
| **产生挂起帧** | ❌ **`agent/loop.py` 里 `Suspension` 零命中**。`_save_checkpoint`（`:1120-1176`）拼 `CheckpointState` 时**根本没传 `suspension`**（`:1153-1171`），永远落 `None`。全仓唯一的挂起帧生产者是**测试**：`tests/test_checkpoint_resume.py:381-400` 手工造 |
| 补做欠账 | ✅ `pending_tool_calls`（`checkpoint/utils/pending.py:50-78`）；`resume()`（`loop.py:484-555`）三道护栏齐；`_complete_pending_turn`（`:1059-1096`）按序补做 + 发事件 + 写帧，那一帧标 `CheckpointSource.SUSPENSION` |
| 审批事件 | ❌ `EventType` 只有 7 个（`stream/utils/types.py:26-39`）。**名字早就预留了** —— `:29-31` 的注释：「HITL 挂起会再追加 `approval_required` —— 该事件由 loop 之外的 / 审批模块产出, 不参与 P0 状态机」 |
| 运行状态 | ⚠️ `RunStatus.WAITING_USER` 已存在（`db/entities.py:78`，注释「挂起等人 (#25 HITL 审批)」）**但没有任何路径能走到它** —— `db/state.py:103` 的 `RUN_STATUS_FOR_OUTCOME` 键只来自 `LoopOutcome`（`agent/utils/types.py:32-47`，6 个值**无挂起档**） |
| 幂等兜底 | ❌ `retry/idempotency.py` 零生产调用方（issue 32 补存储） |

**所以这一片的形状是清楚的：两头都在，中间断成三截。**

## 一、`Decision` 加第三值

**扩展 `Decision` 成三态，不新增平行类型**。理由：`decide()` 的契约是"返回值有语义"，而 `_verdict`（`hooks/registry.py:219-247`）现在对**认不出来的返回值一律 fail-closed 拒绝** —— 多一个平行的返回类型意味着"新类型要显式登记才算数"，而登记漏了就会被静默当成拒绝。加在同一个类型上则相反：**第三种状态是显式的**。

```python
Decision.allow()                            # 放行（今天就有）
Decision.reject("为什么不行 / 该怎么改")      # 当场有结论（今天就有）
Decision.requires_approval(                 # 新增：等着别人给结论
    prompt="这一单要付款了，需要你输一次支付密码",
    needs=("payment_password",),            # 机器可读：还缺什么（框架只透传，不解释）
)
```

**`prompt` 与 `needs` 都是业务语义，框架只搬运** —— 与 `Tool.annotations`、`RunContext.payload` 同一条纪律。

**与 `reject` 的区别必须写进 docstring**（`CONTEXT.md` 已有对应词条）：**拒绝是"当场有结论"，挂起是"等着别人给结论"**。前者把原因当工具结果回填、模型立刻换个说法接着答；后者模型这一轮**不继续**。

### 顺带定一条语义：**拒绝优先于挂起**

今天 `decide()`（`hooks/registry.py:212-217`）是**取第一个非放行者**，而加了第三种值之后，"非放行"有两种。于是**注册顺序会决定用户体验**：业务侧会同时挂"护栏（超预算就拒）"与"需确认"两条规则，确认排在前面时，一个**会超预算**的操作会先弹确认卡、用户确认完才被拒。

**改成：遍历全部 handlers，任一 `reject` 直接生效；一个都没有时才看有没有 `requires_approval`。**

**为什么不靠注册顺序**：顺序是**装配的偶然事实**，而"该不该做"应当是**内容决定**的。issue 37 负责验它（超预算的下单必须直接拒绝，不弹卡）。

## 二、loop 产生挂起帧

### 一次挂起只挂一条（ADR-0014 的边界）

裁决粒度是**一条调用**，而 `_execute_parallel`（`loop.py:370-398`）并行跑同一 assistant 消息发出的一批。所以：

- 遇到**第一条** `requires_approval` → 停，把它放进 `Suspension.pending` 挂起
- **同一批里其余需要审批的调用** → 以「请先处理前一条」的理由回填拒绝（走现成错误通道）
- 理由：`Suspension.pending` 是列表（形状允许一批），但"一次确认配一份一次性载荷"要求载荷归属无歧义（ADR-0014）

### 怎么停下来：`LoopOutcome` 加一个值

`LoopOutcome` 现在 6 个（`FINISHED` / `MAX_TURNS` / `TOKEN_BUDGET` / `TIME_LIMIT` / `TRUNCATION_LIMIT` / `SERVER_INTERRUPTED`），**加 `SUSPENDED`**（或同名之意，实施时定词）。

**不复用现有值**：下游要能区分"这一轮答完了"与"这一轮挂着等人"—— 前者该收尾、后者不该。

### 挂起帧

- `_save_checkpoint` 要**传 `suspension`**（今天 `:1153-1171` 漏了它）
- **`Suspension.reason` 用短标签** `"needs_approval"`（面向机器判断，不是给用户看的文案 —— 用户看的在 `Decision.prompt` 里，由事件带出去）
- **`approval_id` 保持 `None`**（ADR-0014：本项目永远不给它接线）
- **帧的 `source` 要定**：`CheckpointSource.SUSPENSION` 的注释是「**补做**挂起时欠下的工具调用, 因此落下的那一帧」（`checkpoint/utils/types.py:134`）—— 那是**恢复时**的帧，不是挂起那一刻的。挂起帧需要另一个标签（新增枚举值或在 `metadata` 里区分）。**别误用 SUSPENSION** —— 加枚举值会动快照测试，实施时按代价择一。

## 三、事件与服务端

### `approval_required` 是**终局事件**

`EventType` 加第八个成员（名字按注释预留的 `approval_required`）。**它必须是终局事件**：这次 HTTP 请求到此结束，前端据此渲染确认卡并保持输入框禁用。

这意味着**三处同步改**：

| 处 | 位置 |
|---|---|
| 框架的事件类型与终局集 | `stream/utils/types.py` 的 `EventType` 与 `TERMINAL_TYPES`；`RunStream.push` 的 `_closed` 判定（`server/runs.py:91-99`） |
| BFF 的终局集 | `app/minimall/views_bff.py:196` 的 `TERMINAL_EVENTS = {"final","error"}` |
| 前端的渲染表 | `templates/minimall/agent.html:508-548` 的 `RENDERERS`（今天认 7 类） |

事件载荷（**框架只搬运，语义由业务定**）：`tool_call_id` / `tool_name` / `prompt` / `needs`。

**`stream/utils/types.py:29-31` 那条注释要改**：原文说这个事件"由 loop 之外的 / 审批模块产出, 不参与 P0 状态机" —— 本片正是把它变成 **loop 产出、参与状态机**。

### `POST /runs/{run_id}/resume`

与 `POST /runs`（`server/app.py:222-264`）同构：返回一条 SSE 流。

- body：`{decision: "approve" | "reject", data: {...}}`（`data` 可选，一次性载荷）
- **一个端点，不拆 approve / reject 两个** —— 拒绝**也要恢复**：把拒绝原因当**工具结果**回填，模型据此继续（CONTEXT.md 的「恢复」词条：拒绝也要恢复，这与"当场拒绝"是两条路）
- `approve` → `data` 组进本次运行的 `RunContext.payload`（与 `X-User-Id` 同一条路，业务自己取）
- **幂等**：同一 `(run_id, 那条调用)` 的第二次请求必须被 issue 32 的幂等键挡住。**这不是兜底，是主线** —— 恢复是 HTTP 端点，双击 / 重发 / 前端重试都会产生第二次，而重放的是"给这一单付款"

### `SessionRegistry._busy` 的语义要扩（PLAN §6.5 ②）

**问题**：挂起时 run 会 `release(thread_id)`（`server/app.py:689-717` 的 `_finish_run`），于是挂起期间会话"不忙" —— 用户可以发新消息，新 run 从最新快照起跑，**当场撞上那个未决的 `Suspension`**。

**修法**：`_busy` 的语义从「运行中」扩成「运行中 **或** 有未决挂起」。

**判据必须落 PG，不能只靠内存集合**：`sessions.py` 自己写明部署是单进程、重启后内存集合清空。判据就是 ADR-0014 定的那一条：

```sql
charagent_tool_calls.status = 'needs_approval' AND approved_at IS NULL AND run_id 属于本会话
```

- `acquire`（`sessions.py:178-220`）在有未决挂起时抛一个新的错误类型（如 `ThreadSuspendedError` → 409），与 `ThreadBusyError` 分开 —— 前端要能区分"在跑"和"等人"
- `evict_idle`（`:249-269`）**不该淘汰**有未决挂起的会话（它是"正在干活"的另一形态，判据现成）
- 拒绝**新提问**，但**必须放行 `resume` 与 `cancel`** —— 三者的分流在路由函数里做

## 交付物

| # | 内容 |
|---|------|
| 1 | `Decision` 第三值 + docstring 里与 `reject` 的分野 |
| 2 | `loop.py`：`_execute_one` / `_execute_parallel` 认第三值、一次只挂一条、`LoopOutcome` 加挂起档、`_save_checkpoint` 传 `suspension`、挂起帧的 source |
| 3 | `EventType.APPROVAL_REQUIRED` + 终局集；`RunStream` 的 `_closed` 判定 |
| 4 | `POST /runs/{run_id}/resume`（含 `reject` 路径的数据回填）+ 幂等键接线（issue 32 的 store + issue 33 的 `run_id` 传入） |
| 5 | `SessionRegistry`：`_busy` 语义扩展 + PG 判据 + `ThreadSuspendedError` + `evict_idle` 的豁免 |
| 6 | `runs` 行的状态：挂起时**不** `finish` 成终态，落到 `RunStatus.WAITING_USER`（`RUN_STATUS_FOR_OUTCOME` 要能吃下新的 `LoopOutcome` 值） |
| 7 | **`GET /history`（`server/app.py:317` 一带）的响应带一个「本会话是否有未决挂起」的字段**（含 `tool_call_id` / `tool_name` / `prompt` / `needs`，够前端**重建**确认卡）。**这条属框架侧**，但需求来自前端（issue 36 的"刷新恢复"）—— 没有它，刷新页面后确认卡就消失，用户永远没法完成那次代付。判据同上：`charagent_tool_calls.status = needs_approval AND approved_at IS NULL` 且属于本会话。**不新建表、不新建端点** |
| 8 | 用例：拦截→挂起→快照里有 `Suspension`；恢复→只补做那一条、前面的轮次一步不重跑；拒绝→原因当工具结果回填；第二次 resume 被幂等挡住；未决挂起时新提问被拒而 resume/cancel 放行 |

## 验收

- [ ] 一次需要审批的调用之后：`charagent_checkpoints` 那一帧有 `suspension`（`reason` / `pending` 各一条）、`charagent_tool_calls` 那一行是 `needs_approval` 且 `approved_at IS NULL`、`runs` 那一行是 `waiting_user`
- [ ] `approval_required` 事件到达前端，且**它是终局事件**（流正常关闭，不是超时断的）
- [ ] `GET history` 的响应里能读出未决挂起（含重建确认卡所需的四个字段）
- [ ] `resume` 之后：`turn_count` 从挂起处接着数（不从 1 重来）、**前面已完成的轮次一步不重跑**（用 `MockLLM.calls` 断言调用次数）
- [ ] `reject` 之后：模型收到的是**一条工具结果**（拒绝原因），并继续答（不是运行终止）
- [ ] **同一 `(run_id, message_id, tool_call_id)` 的第二次 `resume` 不重放**（幂等键挡住）
- [ ] 未决挂起期间：新提问 → 409 且错误类型与"会话忙"可区分；`resume` / `cancel` 正常放行
- [ ] 进程**重启后**未决挂起仍然拦得住新提问（判据在 PG，不在内存）
- [ ] `pytest` 全绿；`pytest -m pg_db` 通过；`ruff check` / `ruff format --check` 干净

## 备注

- **不动 checkpoint 的恢复机制**：`loop.py:484-555` 的 `resume()` 三道护栏、`pending_tool_calls`、`_complete_pending_turn` **一行都不用改** —— 那条路径早就写好了（`session.resume()` 的 docstring 亲口承认「快照恰好停在『工具调用还没有结果』的半路时（**人工审批挂起点**），loop 还会把那几条欠着的调用补做完再继续」）。本片只补「**拦截 + 挂起**」那一半。
- **不做超时**：挂起的 run 就挂着，用户可以用已有的 `POST /runs/{run_id}/cancel` 收掉。加超时要先有"超时了怎么办"的答案（降级？自动拒绝？），而那需要真实场景（见 ADR-0017 对 #15 的处置）。
- **`runs` 行横跨挂起等待期**这件事**接受**：`finished_at` 会跨几小时是假数据，但真实耗时能从 `charagent_tool_calls` 的 `created_at` / `updated_at` 算出来（零新列）。查询侧的口径已写进 `CONTEXT.md` 的「运行」词条。
- **角色分离（DESIGN #25 提过"发起方不得审批自己发起的挂起项"）本项目不做**：发起方是**模型**，确认人是**买家本人** —— 不存在"自己批自己"。这条要作为"核实后不做"记进收口，别留成沉默的缺席。
