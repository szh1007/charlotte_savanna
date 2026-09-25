# 34 · 框架侧：HITL 挂起-恢复（`Decision` 第三值 + `approval_required` 事件 + `POST /runs/{id}/resume`）

**Status:** done

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

- [x] 一次需要审批的调用之后：`charagent_checkpoints` 那一帧有 `suspension`（`reason` / `pending` 各一条）、`charagent_tool_calls` 那一行是 `needs_approval` 且 `approved_at IS NULL`、`runs` 那一行是 `waiting_user`
- [x] `approval_required` 事件到达前端，且**它是终局事件**（流正常关闭，不是超时断的）
- [x] `GET history` 的响应里能读出未决挂起（含重建确认卡所需的四个字段）
- [x] `resume` 之后：`turn_count` 从挂起处接着数（不从 1 重来）、**前面已完成的轮次一步不重跑**（用 `MockLLM.calls` 断言调用次数）
- [x] `reject` 之后：模型收到的是**一条工具结果**（拒绝原因），并继续答（不是运行终止）
- [x] **同一 `(run_id, message_id, tool_call_id)` 的第二次 `resume` 不重放**（幂等键挡住）
- [x] 未决挂起期间：新提问 → 409 且错误类型与"会话忙"可区分；`resume` / `cancel` 正常放行
- [x] 进程**重启后**未决挂起仍然拦得住新提问（判据在 PG，不在内存）
- [x] `pytest` 全绿；`pytest -m pg_db` 通过；`ruff check` / `ruff format --check` 干净

## 实施记录（2026-09-25）

### 交付物八条

| # | 内容 | 落在哪 |
|---|------|--------|
| 1 | `Decision` 第三值 | `hooks/utils/types.py`：新增 `Verdict`（allow / reject / requires_approval），`Decision` 从「两个字段」变成「三态 + 各自的字段」，构造期逐一校验；`allowed` 变成只读属性（放行之外的两态都不执行工具） |
| 2 | loop 认第三值 + 挂起 | `agent/loop.py`：`_execute_one` 三分支（拒绝→失败回填 / 要人批→不回填 / 放行→执行）、`_handle_tool_turn` 一次只挂一条（同批其余按「请先处理前一条」回填）、`LoopOutcome.SUSPENDED`、`_save_checkpoint` 传 `suspension`、挂起帧的来源是**新增的** `CheckpointSource.APPROVAL` |
| 3 | `EventType.APPROVAL_REQUIRED` + 终局集 | `stream/utils/types.py`（进 `TERMINAL_TYPES`）· `stream/bus.py`（那条「工具没回填完不许终局」的不变量给它开口子）· `agent/utils/events.py`（`approval_required_data` + `emit_terminal` 加一条分支）· `client/render.py`（CLI 那行版式） |
| 4 | `POST /runs/{id}/resume` | `server/app.py`：七步（认证 → 读 body → 查未决 → 认领幂等键 → 组载荷 → 占会话 → 起任务/流成 SSE），拒绝与批准同一个端点 |
| 5 | `SessionRegistry` 的闸门 | `server/sessions.py`：`acquire(context, resuming=)` + 注入的判据 `suspended=` + `evict_idle` 豁免；`ThreadSuspendedError`（409，码与「会话忙」分开） |
| 6 | `runs` 行落 `waiting_user` | `db/state.py`（`RUN_STATUS_FOR_OUTCOME[SUSPENDED]` + 新常量 `SETTLEABLE_RUN_STATUSES`）· `db/repositories/runs.py`（`finish` → `settle`，非终态不写 `finished_at`） |
| 7 | `GET /history` 带未决挂起 | `server/history.py`（`pending_approval_row` + 五个字段名）· `server/app.py`（响应多一个 `pending_approval`，恒在，没有时 null）· `db/repositories/tool_calls.py`（`list_pending_approvals`：join 回会话 + 两列判据） |
| 8 | 用例 | 见下（离线 32 条 + 真库 8 条） |

**除了票据点名的八件，还补了三处**（不做就是半截账）：

- **`charagent_tool_calls` 加两列**（`approval_prompt` / `approval_needs` + 迁移 0005）：
  票据第 7 条要的「够前端重建确认卡」四个字段里，`prompt` 与 `needs` 在库里**没有落点**
  —— 它们只有挂起那一刻在内存里。而挂起态的家就是那一行（ADR-0014），于是把它们记在
  那一行上：`approved_at` 管「批没批」，这两列管「问什么」。刷新页面之后 `GET /history`
  靠它们把卡重建出来。
- **恢复会重新装配一次会话**（`acquire(resuming=True)` 丢掉缓存的那个会话对象）：ADR-0015
  的密码通路是「`data` → `RunContext.payload` → 装配时进工具闭包」，而会话是按 thread
  缓存的（业务只在第一次装配）—— 复用旧会话等于把那份一次性载荷丢掉。代价很小：会话的
  内存状态本来就从快照来（那正是 resume 的定义）。
- **`resume()` 缺人的结论就报错**（`LoopConfigError`）：命令行 `--resume` 撞上一帧挂起点时
  原先会**直接补做**那条调用 —— 而它可能就是「给这一单付款」。DESIGN #25 写着「绝不自动
  执行」，于是没有 `approval` 就不补做。issue 33 记的「命令行从挂起点续跑今天到不了」在
  本片变成了「到得了，但会被拦住」（挂起点从本片起真的存在了）。

### 两处改判（都写进了代码注释）

1. **拒绝优先于挂起**（票据已定）：`HookRegistry.decide` 从「取第一个非放行者」改成
   「遍历完，任一拒绝直接生效；一个都没有时才看有没有要人批的」。顺序是装配的偶然事实，
   「该不该做」应当是内容决定的 —— 否则一个会超预算的操作会先弹卡、用户输完密码才被拒。
2. **ticket 33 的「第二段不碰那一行」→「第二段写这一段的结局」**：挂起那一段自己也要写
   （写的是 `waiting_user`，不是终态），否则「它现在在等人」在库里没有落点；而「还没结束」
   由 `finished_at IS NULL` 表达。唯一的例外是**失败/取消那一段不碰**（那一次运行还开着，
   写 `failed` 会把还能恢复的运行判死）。`test_client_resume_db.py` 里那条用例的 docstring
   记了这次改判。

### 真机（2026-09-25，本机 Postgres + 真模型，脚本 `D:/__WorkSpace__/Temp/hitl34_real.py`）

先给开发库应用了迁移 0005（`alembic upgrade head`：`0005_tool_call_approval_columns`）。

会话 `hitl-34-real-1790328284`，一件玩具高危工具（`pay_order`）+ 一条「高危就要人批」的核查插件：

| 步 | 期望 | 实际 |
|---|---|---|
| 问「帮我付了订单 …」 | 流停在确认 | `['reasoning', 'tool_call', 'approval_required']`；载荷 `tool_name=pay_order` / `prompt` / `needs=['payment_password']` |
| 查库 | 三处都对 | `runs: waiting_user / finished_at=None / turn_count=1`；`tool_calls: needs_approval / approved_at=None`；帧 `suspension.reason=needs_approval`、`pending=[{id,name,arguments}]`、`source=approval` |
| `GET /history` | 四样齐全 | `run_id` / `tool_call_id` / `tool_name` / `prompt` / `needs` ✓ |
| 未决期间新提问 | 409 且码可区分 | `409 thread_suspended` |
| 批准（带载荷） | 工具真跑 + 收尾 | 流 `['tool_call', 'tool_result', 'final']`；工具跑了一次；**装配过的载荷**：`[{}, {'payment_password': '888888'}]`（第二次装配才看得见载荷 ✓）；`runs: finished`；`tool_calls: succeeded + approved_by=u-1 + approved_at`；刷新后 `pending_approval=None` |
| 第二次 resume | 不重放 | `404 run_not_found`；工具总跑次数 **1** |
| 再挂一次 → cancel | 收得掉 | `200 {'status': 'cancelled'}`；刷新后 `pending_approval=None`；之后新提问 `200` |

> **真机跑了三遍，第三遍才发现一个缺陷**（前两遍的输出里有一条被我当成「上一次尝试的
> 残留」的 traceback，其实是同一个会话里的）: **取消一次挂起之后再问一句话，模型 API 回
> 400**（`assistant message with 'tool_calls' must be followed by tool messages`）。根因:
> 取消只改了库，而**会话内存**里那份历史仍停在「欠着那条调用的结果」的半路上 —— 那种
> 形状在 MockLLM 面前看不出来（它不校验配对），只有真上游会当场拒。修法:
> `POST /runs/{id}/cancel` 收掉挂起时把这段会话从登记表里**丢掉**（`SessionRegistry.forget`），
> 下一次提问重新装配并从快照水合 —— 那时 `_seal_pending_calls` 会给那条调用补一条
> 「结果未知」的回填，请求又是配对的. 补了一条离线用例盯**发给模型的那份历史是否配对**
> （旧用例只断状态码，它一直是绿的）.

`turn_count` 在恢复后是 **3**（挂起那轮 1 + 补做那轮 2 + 模型作答 3）—— 补做那一轮算一轮
（它同样落一帧），这是 ticket 22 起就有的口径，票据那条验收要的「不从 1 重来、不重跑」
都成立。那几行数据是这次的证据，留在开发库里；要清就
`DELETE FROM charagent_threads WHERE thread_id LIKE 'hitl-34-real-%'`（运行 / 消息 / 帧 /
工具调用跟着 CASCADE 走）。

### 用例（新增 42 条）

| 文件 | 断的是什么 |
|------|-----------|
| `tests/test_hooks.py`（+4） | 三种形状各自的构造期校验 · 纯是/否确认的 `needs` 为空合法 · 要人批那条原样带回（取第一个） · **拒绝优先于挂起（两个注册顺序各一遍）** |
| `tests/test_loop_suspension.py`（新文件，12 条） | 要人批 → 不执行 + 终局事件是 approval_required（含四个字段）· 挂起帧（来源 / suspension / 轮数）· 同批只挂第一条 · 同批普通调用照跑 · 拒绝不是挂起 · 批准才执行（轮数接着数、不重跑）· **恢复时人批的抵消、护栏的拒绝照常生效** · 拒绝回填原因并继续答 · 缺省拒绝文案 · 没有结论就报错 · 普通快照无需结论 · 挂起不泄漏到下一段 |
| `tests/test_server_approval.py`（新文件，12 条） | 事件流 + `/history` 的卡 · 批准 / 拒绝两条路 · `decision` 非法值 400 · **重复提交两种时相（已认领 / 已办完）各 409** · 没有挂起 404 · 未决期间 409（码与「会话忙」不同）而 resume/cancel 放行 · **取消挂起后闸门放开、且下一次提问发给模型的历史是配对的**（真机那个 400 的回归）· **认领之后失败会把键放回去** · 没配库这条路由不存在 · **真并发两下只跑一次** |
| `tests/test_server_approval_db.py`（新文件，4 条） | 全流程真 SQL · **重启后仍然拦得住** · 别人的会话碰不到（join）· 幂等键真的落进 `charagent_idempotency_keys` |
| `tests/test_server_sessions.py`（+5） | 挂着不许新提问而恢复放行 · **恢复会重新装配** · 等人的会话不淘汰 · 没注入判据就没有这道闸门 · **`forget` 按段丢掉缓存**（取消挂起那条路用的） |
| `tests/test_db_store.py`（+4） | 未决那条查询（按会话 + 两列判据 + 别人的查不到）· 批过/做完/取消的都不再算未决 · `record_decision` 只记「谁批的」不动状态 · 推进状态时补写「要问什么」 |
| `tests/test_db_recorder.py`（+1） | 挂起那一段的收尾：运行行 `waiting_user`（无结束时刻）+ 工具调用行带话术与缺失项 |
| `tests/test_client_session.py`（+1）与 `test_client_resume_db.py`（+2） | 会话那一层走一次挂起→恢复（第一段交出 SUSPENDED、第二段执行）· 真库里 `waiting_user` → 恢复后 `finished` 且**只有一行** |
| 既有用例的改动 | `test_checkpoint_resume.py` 4 条挂起恢复用例补 `approval=`（没有结论就不补做，那正是本片加的护栏）· `test_client_session.py` 的续段用例改成「settle 了」· `test_db_store.py` 的 `finish` → `settle` 与版本断言 · `test_client_render` / `test_stream` / `test_db_entities` / `test_root_facade` 的契约清单跟着加成员 |

**十三条伪证（把实现改坏，看用例红不红，逐条都红）**：挂起帧不写 `suspension` · 恢复时
不再抵消「需人工确认」（又挂了一次）· 没有结论也照常补做 · 恢复端不再认领幂等键 ·
挂起那条事实写成 `pending`（真库用例）· 状态映射改成 `finished` · 闸门失效 ·
批完不记「谁批的」· 拒绝也去执行工具 · **认领之后失败不再把键放回去** ·
取消挂起不再写那一行 · 幂等存储的 `claim` 直接放行（真库用例）·
**取消挂起不再丢掉那份会话缓存**（真机那个 400 的回归）。

**命中一处冗余**（伪证的副产物）：`_write_calls` 原先在建行那一拍也写「要问什么」，而
建行那一拍**永远拿不到**它（裁决还没发生，事实是 PENDING）—— 那一笔是死代码，删掉了；
真正写它的是推进状态那一笔（幂等插入会跳过已存在的行，所以只有它能写）。

### 两轴复核（`/code-review`）后的修补

- **规范轴**：两处「半角括号后直接贴中文」（`db/repositories/idempotency.py` 与
  `0004_idempotency_keys.py`，都是 issue 32 留下的、这次一起收掉）· `ApprovalBookkeeping.calls`
  的 `| None` 是死守卫（唯一构造点就在「有库」那条分支里）→ 去掉 · 幂等键的格式串在用例里
  抄了三遍且有一句注释说「用例直接借它」（不实）→ 每个文件留一处并改成说真话的注释
  （**故意钉住格式**：格式一变那两条用例当场红）· `test_loop_suspension.py` 里指向
  `test_server_app.py` 的指针过时（服务端那一半在新文件里）→ 改了 · 四处裸 `-> list:`
  → 补元素类型。
- **规格轴**：八条交付物、九条验收**没有缺失项**；三处偏差里两处是真的并已修：
  ① `resume_run` 的 docstring 抬头与代码相反（写成「先占会话、后认领」，实际是先认领、
  失败时放回去）→ 重写那七步；② `approval_required` 载荷多一个 `turn`（docstring 自称
  「四个字段」却返回五个）→ docstring 改成「四样 + 轮次」（`turn` 与其它事件同一条惯例，
  issue 36 的渲染要按它定位）。第三处（验收那句「幂等键挡住」与实际三种时相的分工）记进
  上面的「残留」，代码不动。

### 残留（都已记进代码注释或下一片）

- **两次 `resume` 的三种时相各由谁挡住**（验收那条写的是「幂等键挡住」，实际是这样分工的）：
  **第一次还在跑**（真并发 / 双击）→ 幂等键答 `409 approval_in_progress`（它管的正是
  「有人在办」，而且是**跨进程**的那一个 —— 重启之后内存里的「忙」早没了）；**已经跑完**
  → 那一行已推进成终态，查到的是「没有未决挂起」→ `404 run_not_found`（与「压根没这回事」
  同一条回答）；**进程死在恢复的半路** → 键卡在「在办」→ `409 approval_in_progress`（同一
  把键的跨进程语义）。三种都**不重放**，差别只是前端的话术；issue 36 按码分文案时要知情
  （流程上「先认领键、后占会话」，就是为了让并发那一相由键回答，而不是被本进程的「会话忙」
  顺带答一句）。
- **幂等键不配 TTL**（刻意的）：一把会过期的键等于「过一会儿可以再点一次」，而重放的是一笔
  付款。卡住时的明路是**取消这次挂起重新发起**（`POST /runs/{id}/cancel`，本片已实现）。
- **挂起那一段照样算钱**：金额按当时的价目表写上去，恢复那一段收尾时按累计用量重算覆盖
  （`runs.settle` 的既定语义：重算不是累加）。
- **不做超时 / 不做角色分离**：与票据备注一致，理由见 note 的处置；`Suspension.approval_id`
  保持 `None`（ADR-0014）。
- **issue 35 起才有的业务侧**：`pay_my_order` 工具、真正的支付端点、`needs` 里那个
  `payment_password` 怎么取 —— 本片只保证框架把载荷送到**装配那一刻**（真机已验证）。

## 备注

- **不动 checkpoint 的恢复机制**：`loop.py` 的三道护栏、`pending_tool_calls`、`_complete_pending_turn` 的补做顺序**一行没改** —— 本片只给它加了「必须有人给的结论」这一道前置。
- **不做超时**：挂起的 run 就挂着，用户可以用已有的 `POST /runs/{run_id}/cancel` 收掉（本片把它扩到「挂起中」那一种：见 `_cancel_suspension`）。加超时要先有"超时了怎么办"的答案（降级？自动拒绝？），而那需要真实场景（见 ADR-0017 对 #15 的处置）。
- **`runs` 行横跨挂起等待期**这件事**接受**：`finished_at` 会跨几小时是假数据，但真实耗时能从 `charagent_tool_calls` 的 `created_at` / `updated_at` 算出来（零新列）。查询侧的口径已写进 `CONTEXT.md` 的「运行」词条。
- **角色分离（DESIGN #25 提过"发起方不得审批自己发起的挂起项"）本项目不做**：发起方是**模型**，确认人是**买家本人** —— 不存在"自己批自己"。这条要作为"核实后不做"记进收口（issue 38），别留成沉默的缺席。

## 改了哪些文件

| 文件 | 改了什么 |
|------|---------|
| `CharAgent/hooks/utils/types.py` | `Verdict` + `Decision` 三态（字段与校验重写） |
| `CharAgent/hooks/registry.py` | `decide` 的拒绝优先 + docstring |
| `CharAgent/hooks/__init__.py` · `CharAgent/__init__.py` | 门面导出 `Verdict` |
| `CharAgent/agent/utils/types.py` | `LoopOutcome.SUSPENDED` · `ApprovalRequest` / `Approval` / `APPROVAL_REJECTED_TEXT` · `ToolCallFact` 两列 · `LoopState` / `LoopResult` 的 `approval` |
| `CharAgent/agent/utils/events.py` | `approval_required_data` + `emit_terminal` 的挂起分支 |
| `CharAgent/agent/utils/messages.py` | `APPROVAL_ALREADY_PENDING_TEXT`（同批第二条要批时的回填文案） |
| `CharAgent/agent/loop.py` | `_execute_one` / `_execute_parallel` / `_handle_tool_turn` / `_facts_of` / `_complete_pending_turn` / `resume` / `_run` / `_save_checkpoint`（挂起与恢复的全部逻辑） |
| `CharAgent/checkpoint/utils/types.py` | `CheckpointSource.APPROVAL` · `SUSPENSION_REASON_APPROVAL` |
| `CharAgent/stream/utils/types.py` · `stream/bus.py` | 第八类事件 + 终局集 + 那条不变量的口子 |
| `CharAgent/client/session.py` | `resume(run_id=, approval=)`；成功那一段一律写结局（失败那一段仍不碰） |
| `CharAgent/client/render.py` | `approval_required` 的终端版式 + `SUSPENDED` 的结束语 |
| `CharAgent/db/state.py` | `SUSPENDED → WAITING_USER` + `SETTLEABLE_RUN_STATUSES` |
| `CharAgent/db/repositories/runs.py` | `finish` → `settle`（支持非终态、`finished_at` 只在终态写） |
| `CharAgent/db/repositories/tool_calls.py` | `list_pending_approvals` · `record_decision` · `set_status` 补写「要问什么」· `build_tool_call` / `add` 两列 |
| `CharAgent/db/schema.py` · `alembic/versions/0005_tool_call_approval_columns.py` | 工具调用表两列 + 迁移 |
| `CharAgent/db/recorder.py` | `runs.settle` 的调用点 + 推进状态那一笔带「要问什么」 |
| `CharAgent/db/entities.py` · `db/__init__.py` | 实体字段说明 + 导出新常量 |
| `CharAgent/server/utils/errors.py` | `ThreadSuspendedError` · `ApprovalAlreadyHandledError` |
| `CharAgent/server/sessions.py` | `acquire(resuming=)` + `suspended=` 判据 + `evict_idle` 豁免（改异步）+ `forget`（取消挂起时丢掉那份缓存） |
| `CharAgent/server/history.py` | `pending_approval_row` + 字段名 |
| `CharAgent/server/app.py` | `POST /runs/{id}/resume` + 取消挂起那条分支 + `pending_approval` + 装配（幂等登记簿 / 判据） |
| `CharAgent/server/__init__.py` | 导出 `PENDING_APPROVAL_FIELD` |
| `CharAgent/retry/idempotency.py` · `db/README.md` · `db/database.py` · `alembic/env.py` | 文档：幂等键的第一个真实调用方从「计划」改成「已接线」 |
| `CharAgent/tests/*`（9 个既有 + 3 个新） | 见上表 |

**没动的**：`checkpoint/` 的恢复机制（`pending_tool_calls` / `_seed_from_checkpoint` 一行没改）·
`retry/` 的协议与两个存储实现 · `ask()` 的记账路径 · `ChatSession` 公开三动作的签名（只是
`resume` 多了一个可选参数）· `client/app.py`（命令行那条路照旧：撞上挂起点会以一条清楚的
错误停下）。
