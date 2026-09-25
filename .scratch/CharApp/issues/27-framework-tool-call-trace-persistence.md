# 27 · 框架侧：工具调用轨迹落库 + 记录层改为「产生即落库」（`charagent_tool_calls` 的第一个生产调用方）

**Status:** done

**Type:** task

**Blocked by:** 无

**上游:** `CharAgent/docs/DESIGN.md` #41（trace 标准：span 层级 run → turn → tool_call）与 #38（日志结构化 + 全链路关联）；`CharApp/docs/PLAN.md` §5 的 L3a 段（验收："CLI 能列出**每一次工具调用**"）

## 一片一句话

给 `charagent_tool_calls` 装上第一个生产写入方。而要做成这件事，记录层的写入路径必须从「收尾一次性写」改成「**产生即落库**」—— 提问那一行在提问时写、每轮产生的消息随轮写 —— 工具调用行才能按它本来的语义写：**执行前落 `pending`、执行后回填结果**。

## 为什么这两件事必须一起做（2026-09-24 实施期定案）

`charagent_tool_calls` 的 `message_id` 是**非空复合主键之一 + 外键指向 `charagent_messages`**（`db/schema.py:364-450`，注释原文「**不能为空**」）。写一行调用，发起它的那条 assistant 消息行**必须先在库里**。而今天：

- `messages` 行只在运行收尾才写（`db/recorder.py` 的 `_write` 调 `add_lines`）
- `message_id` 是**插入时才生成的随机 `uuid4().hex`**（`db/repositories/messages.py:147`）

于是**运行期间写一行调用，在类型上就做不到**。本片最初记了三条候选（a 让 id 可推导 / b messages 按轮增量写 / c 只记挂起调用），实施时与用户逐条核对后定 **b**：

| 候选 | 为什么不选 |
|---|---|
| a 只让 id 可推导，写入时机不变 | 运行中要写那条 assistant 行 → 运行期间 `charagent_messages` 出现半截行（提问行还没写，assistant 行先到）→ 审计视图里「提问」会排在「我要调工具」**之后**（created_at 错序） |
| c 只在挂起时写调用行 | 不成立：挂起那条同样撞外键，且「挂起」与「普通」两套写法必然漂移 |
| **b 产生即落库（选定）** | 外键矛盾自然消失；顺带修掉三件事，见下 |

**b 顺带修掉的三件事**（前两个方案都绕不过）：

1. `created_at` 变成**真的产生时刻**（列注释本来就这么写；今天是收尾那一刻 + 微秒偏移）
2. 运行中刷新页面能看到自己刚问的那句；进程硬杀时库里有真实进度（而不是一条都没有）
3. **同一次 run 的第二段**（审批恢复，见 issue 33/34）能**直接寻址**第一段写下的那一行 —— 因为 id 派生自 `(run_id, 下标)`，不必反查

## 写入路径（实施定案）

| 时机 | 写什么 | 谁触发 |
|---|---|---|
| 提问 | 提问行（可见） | `ChatSession.ask`，在 loop 之前 |
| 每轮 · **执行前** | 那条 assistant 隐藏行 + 本轮每条调用 `pending`（挂起那条写 `needs_approval`，归 34） | loop（`_handle_tool_turn`） |
| 每轮 · **执行后** | tool 回填行 + 每条调用终态（`succeeded` / `failed` + `result` + `duration_ms`） | loop（每轮收尾那一拍） |
| 收尾 | **补齐**（哪一轮没写上就补，幂等）· **修订**（截断续写时片段行改隐藏、拼合正文写进最后一条）· `runs.finish`（终态 / token / `last_checkpoint_id`）· 会话活动时刻 | `ChatSession` → `ConversationRecorder` |

> **实施期修正**：初稿把 `_complete_pending_turn`（恢复时补做那一轮）也列成「执行前那一拍」的触发点，实施核实后**它不需要** —— 那一轮的 assistant 行与调用行在**上一段**就写过库了（挂起那一刻落的就是它们），此刻再交一遍会被幂等写全部跳过。补做只走每轮收尾那一拍，记录层按 `message_index` 推进原来那一行。

四条纪律：

- **`message_id` 可推导**：`(run_id, 该条消息在本次 run wire 历史里的下标)` 派生；同一行写两次是幂等的（`ON CONFLICT DO NOTHING`）
- **`runs` 仍是两笔**（开始建行 + 收尾补余下）—— issue 22 已有的形状，本片不动它
- **收尾的「补齐」是安全网**：某一轮写失败只降级（记一笔、这一轮照跑），收尾那一趟把它们补上；收尾本身失败仍走今天那套 `_missed` + 提示行
- **不是「每条消息一写」**：一轮里的 assistant 行与它的 tool 回填行之间只隔着工具执行（几十毫秒），逐条写等于把 DB 往返塞进工具执行热路径；每轮写两次（执行前 / 执行后）拿到的行完全一样，而挂起那一轮**是完整的一轮**，`needs_approval` 照样在挂起那一刻落库

**通道**：`AgentLoop` 新增一个**可选的落库协作者**（协议定义在 agent 侧 —— `agent/` 不 import `db/`，且 `db/state.py` 反向依赖 `agent/utils/types.py`，双向会成环）。没给、或业务自己的记录员没实现它，就退回今天的「收尾一次性写」，行为与从前逐字一样。

**普通调用那一行 `pending` 只存在几十毫秒**（工具执行那一下）—— 它兑现的是「执行中被硬杀 / 被取消时，库里看得出它正要调什么」；挂起那条的 `needs_approval` 才会一直等在那里。

## 交付物

| # | 内容 |
|---|------|
| 1 | `MessagesRepository`：`message_id` 由 `(run_id, 下标)` 派生（`add_lines` 收显式 id 列表）· 重复写跳过（upsert）· 修订用的单行更新；用例钉住「同一轮写两次不产生两行」 |
| 2 | `ToolCallsRepository`：补写幂等（已存在则跳过）+ 推进终态（`result` / `duration_ms` / `approved_by` 走现成的 `set_status`） |
| 3 | `ConversationRecorder`：新增**增量落库**方法（提问行 / 每轮那两拍）+ 收尾改成「补齐 + 修订」；`runs` / `threads` 那两步不动 |
| 4 | `agent` 侧：工具调用**事实**类型（`tool_call_id` / `tool_name` / `arguments` 原样 JSON / 状态 / 结果 / `duration_ms` / 发起它的那条消息的下标）挂进 `TurnRecord`；loop 在两处把这批事实与新增消息交给落库协作者 |
| 5 | `ChatSession`：装配这个协作者；提问那一刻先把提问行落库 |
| 6 | 挂起那条调用的写入路径（`needs_approval`）—— **本片只写状态，不产生挂起**（产生挂起归 issue 34），用一条用例钉住「能写进去且 `approved_at` 为空」 |
| 7 | 框架侧用例：一次带工具调用的运行结束后，`list_for_run` 能列出**每一次调用**且字段齐全；**工具失败与被护栏拒绝两种情况都要有一行**（它们同样是一次调用）；增量写与收尾补齐两条路各自幂等 |

## 验收

- [x] 一次真实运行后，`charagent_tool_calls` 里**每一次工具调用一行**：`tool_name` / `arguments`（原样 JSON）/ `result` / `duration_ms` / `status` 全部有值 —— 真机验过（CLI + 真 PG + 真模型，会话 `issue27-check`：2 条调用各一行，`query_order_status` / `convert_length`，状态 `succeeded`，耗时 1 / 0 ms）
- [x] 被护栏**拒绝**的调用也有一行（`status = failed`，`result` 是拒绝原因）—— 它是"模型想做什么"的证据，不能漏（`tests/test_loop_trace_sink.py` 的 `test_a_denied_call_is_handed_over_as_a_failure_with_the_reason`）
- [x] 参数是**原样 JSON 字符串**（`arguments` 列不做预解析，畸形 JSON 正是自纠错路径的信号——见 `db/schema.py` 那列的注释）
- [x] 运行中（还没收尾）库里已经有：提问行 · 那条 assistant 隐藏行 · 每条调用的行（`pending` 或终态）—— 真机边跑边查的时序：2.75s 时提问行已落库（模型还在思考）、3.54s 时 assistant 隐藏行 + 两条 tool 行 + 两条调用行落库、4.47s 时最终答复行落库
- [x] 同一轮写两次不产生两行（幂等用例）；某一轮没写上时，收尾那一趟把它补齐
- [x] 截断续写的一次运行结束后，库里可见答复是**拼合后的完整正文**（不是尾段），早先的片段行是隐藏行
- [x] `runs` / `threads` 两张表的既有行为一字不改；messages / tool_calls 的**读**行为一字不改（既有 1052 条用例原样通过）
- [x] `pytest -m pg_db`（真库）通过 —— 54 passed
- [x] 无单价 / 无金额：本片**不碰** `total_cost`（那是 issue 28）

## 改判记录（2026-09-24，实施期）

| 原文 | 改判为 | 理由 |
|---|---|---|
| 验收「写入不影响现有的 1052 条用例；`runs` / `messages` / `threads` 三张表的既有行为**一字不改**」 | 「`runs` / `threads` 一字不改；messages / tool_calls 的**读**行为一字不改，**写**时机改为产生即落库」 | 这条与本片的目的直接冲突 —— 增量落库**就是**改 messages 的写时机 |
| 交付物「只在收尾写」（初版倾向 a） | 改为「产生即落库 + 收尾补齐」 | 见「为什么必须一起做」那张表 |
| 通道「`TurnRecord` 加字段 **或** 注入回调」 | 两者都用了：事实挂 `TurnRecord`（收尾补齐读它），loop 另有落库协作者（增量写用它） | 事实只有一个来源，两个消费方 |

## 实现记录（2026-09-24 完工）

**改动落点**（19 个文件）：

| 层 | 文件 | 改了什么 |
|---|---|---|
| db | `repositories/messages.py` | `message_id_for(run_id, 下标)`；`add_lines` 收显式编号 + `ON CONFLICT DO NOTHING`（幂等）；新增 `update_line`（收尾修订用） |
| db | `repositories/tool_calls.py` | `add_calls` 改成幂等（已有则跳过，**不覆盖已有结论**） |
| db | `recorder.py` | 新增 `flush`（`TraceSink` 的实现，运行中途那两拍）；`record` / `record_unfinished` 改成**补齐 + 修订**；`_write_calls` 两笔（先 pending 建行、再推进终态） |
| db | `state.py` | `TOOL_CALL_STATUS_FOR_OUTCOME` 映射表 + `tool_call_status_for_outcome`（与 `LoopOutcome → RunStatus` 同构） |
| agent | `utils/types.py` | `ToolCallOutcome` / `ToolCallFact` / `TraceSink`（`runtime_checkable`）；`TurnRecord.calls`；`LoopState.flushed` |
| agent | `loop.py` | `trace_sink=` 构造参数；`_flush`（唯一交付口）；`_handle_tool_turn` 执行前那一拍 + 返回事实；`_record_turn` 收尾那一拍；`_facts_of` 纯函数 |
| client | `session.py` | 装配 `trace_sink`（按 `isinstance(recorder, TraceSink)`）；`ask` 里提问当场落库；`record_unfinished` 传 `since` |
| 门面 | 三处 `__init__.py` | 新增名字上浮（`ToolCallFact` / `ToolCallOutcome` / `TraceSink` / `message_id_for`） |
| 测试 | `test_loop_trace_sink.py`（新）· `test_db_recorder.py` · `test_client_session.py` · `test_db_conversation.py` · `test_db_state.py` · `test_db_store.py` · `doubles.py` | 交付形状 / 增量写 / 幂等 / 补齐 / 修订 / 挂起写入路径 / **一次真跑的端到端**（loop → 记录员 → `list_for_run`）/ 两条 transcript 出口逐条对位 / 假库学 upsert |

**实施期发现并修掉的三处**（都不是顺手，是验证时真踩到的）：

1. **说明行会占掉 wire 派生的编号**：`record_unfinished` 的「这一轮没答完」那句与提问行一起排位置，于是它会拿到 `run_id:since+1` —— 而那个编号可能已经被这一轮真产生的消息占了（跑到第二轮才失败的运行），幂等写于是把说明行**当成已写过跳过**，用户看不到那句话。修法：只有**来自 wire 历史**的几条（`produced` 给出的那一段）才用派生编号，补进来的内部件给随机编号。用例 `test_the_unfinished_notice_does_not_take_a_wire_index` 钉住（退回旧写法它当场红）。
2. **两拍重复投递同一条消息**：先写的 `_handle_tool_turn` 若不推进 `state.flushed`，`_record_turn` 那一拍会把 assistant 行再交一次（幂等所以不致命，但「一条消息交一次」这条口径就破了）。修法：执行前那一拍把 `state.flushed` 推到那条之后。
3. **假库不认 `ON CONFLICT DO NOTHING`**：不补的话幂等用例是假绿（同一批写两次在假库里会出两行）。修法：`FakeRecordSession.execute` 按方言子句跳过已有的主键。

**验证**：`pytest` 1071 passed（原 1052 + 新 19，零失败）· `pytest -m pg_db` 54 passed · `ruff check` / `ruff format --check` 干净 · 真机两次运行（`issue27-check` 跑完看字段齐全；`issue27-live` 边跑边查，时序如上）。**这两个是本地开发库里的验证数据，可以随手清掉**。

**留给后面两片的边界**（实施时核实，写在这里免得忘）：

- **issue 33**：`resume()` 记账时，若补做的那条调用**不属于本次 run**（CLI `--resume` 开的是新的一次运行，而快照里那条 assistant 行是上一次运行写的），它的 `message_id` 在本次 run 里没有对应的消息行，外键会拦下（降级成一条 warning，不致命）。两条路可选：HITL 恢复沿用挂起那次运行的编号（本来就是同一次运行，本片已让它能直接寻址），或者把那条 assistant 行按本次运行补写一行。写在 `_write_calls` 的 docstring 里了。
- **issue 34**：挂起那条的**初始状态**也走 `pending`（先建行再推进到 `needs_approval`）—— 因为「结论」不该是一行的第一个状态（`ToolCallStatus` 的注释就是这个意思）。挂起那一刻的行由**执行前那一拍** + `_write_calls` 的推进写出，所以 `needs_approval` 在挂起时就已经在库里。
- **「工具执行中被取消」（Ctrl-C / kill switch）**：库里会留下一行 `pending`，而它**永远不会被推进**（那一轮没有收尾那一拍，`LoopResult` 也不存在）。这一版**认下**：`pending` 的语义是「没有结论」，比编一个 `failed` 诚实；要真正收口得让取消那条路把在飞的调用标成 `cancelled`（状态值早就在 `ToolCallStatus` 里备着），那是另一件事 —— 归 issue 33/34 或更后面的片，别当成本片的漏项。

## CodeReview（2026-09-24）与处置

两条轴各派一个 sub-agent 独立跑（Standards：仓库成文规范 + 气味基线；Spec：票据逐条核对），结论与处置如下。

**Standards 轴**

| 发现 | 处置 |
|---|---|
| 新增文本里有 1 处全角句号、5 处代码跨度后漏空格（违反 `CLAUDE.md` §4.9） | **已改**（那 6 处） |
| `ADR-0009` 与 `PLAN.md` 仍写着「`charagent_tool_calls` 至今没有生产调用方」 | **不做**：ADR 是「当时那条决定」的记录，改它等于改历史；`PLAN.md` 的现状表按惯例由**收口片**（issue 31 · L3a 收口）补记。这里记为**已知待办**，不是漏项 |
| `AgentLoop._flush` 与 `ChatSession._flush` 同形（判空 + 委托） | **留着**：仓库自己的 DRY 阈值是「重复 ≥ 3 次抽取」，这是 2 处，且两处的判空条件不同（一个判 `LoopState.run_id`，一个判参数）。顺手把 loop 那份 docstring 里「交付只该有一处判」的**过头话改准**（loop 的两拍一处判；会话那条路是另一条，没有 `LoopState` 可判） |
| `add_lines` 不再走 `add_messages`，messages 表因此有两条插入路径 | **已改**：`add_messages(rows, *, skip_existing=False)`，`add_lines` 调它并传 `skip_existing=True`（一条路径 + 一个显式开关） |
| `raw` / `lines` 两个 `Sequence[TranscriptLine]` 靠名字分不出谁是谁 | **已改**：`raw` → `produced`（「产生时那一拍的形态」，与「目标形态」对举） |
| `start` / `since` / `message_index` 是同一个量（run 内下标）的三个名字 | **留着**：三处各自读起来都通顺（批次起点 / 从第几条起记 / 发起消息的下标），合成一个类型的收益不抵多一层间接。记在这里，将来若真的错了一次编号再抽 |

**Spec 轴**（结论：交付物 7 条兑现，四条纪律逐条成立）

| 发现 | 处置 |
|---|---|
| 写入路径表把 `_complete_pending_turn` 也列成「执行前那一拍」的触发点，实际没写 | **核实后判定不需要**（那一轮的行上一段已落库，再交一遍全被幂等跳过）→ 补了方法 docstring + 票据表格改判（见上） |
| 交付物 #7「一次带工具的运行结束后 `list_for_run` 能列出每一次调用」缺**端到端**用例 | **已补**：`test_a_real_run_ends_up_listable_by_run`（真 loop → 真记录员 → 假库 → `list_for_run`，一成一败两次调用） |
| `_revise` 依赖「`recorded_transcript` 与 `visible_transcript` 逐条对位」这条前提，而无用例钉住 | **已补**：`test_the_two_transcript_exits_stay_line_for_line`（条数、顺序、`tool_call_ids` 三条） |
| 「执行中被取消」留下永不推进的 `pending` 行 | **记为本片边界**（见上），不当漏项 |

## 备注

- **它与 ADR-0016 是一对**：轨迹保留原文（订单号、地址、余额进 PG）是那条 ADR 的决定，本片是它的执行者。**别在本片里加脱敏** —— 那会当场推翻 ADR-0016。
- **注意 ADR-0006**：迁移历史已在 2026-09-23 压成一条（`0001_core`），本片**只动 `schema.py` 的注释（若动）**，**不加迁移**（`charagent_tool_calls` 表已经在了，列一个都不加）。
- `tests/test_db_schema.py` 里的表结构断言（每列必须有 `comment=`）对本片不构成负担（表没变），但**加列就要加注释**那条纪律照旧。
- 与 issue 28 的分工：本片只管**把事实写进去**（谁、什么时候、什么参数、什么结果、多久）；"折成多少钱"是另一片。
- 与 issue 33 的分界：本片把**增量落库的形状**落定（含「同一次 run 的第二段能不能直接寻址」这条能力），33 负责让 `resume()` 真的用它记账。
- 与 issue 34 的分界：本片提供 `needs_approval` 的**写入路径**（用例钉住），34 负责产生它（`Decision` 第三值 + 挂起帧 + `runs` 转 `waiting_user`）。
