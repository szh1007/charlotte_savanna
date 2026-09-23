# 27 · 框架侧：工具调用轨迹落库（`charagent_tool_calls` 的第一个生产调用方）

**Status:** todo

**Type:** task

**Blocked by:** 无

**上游:** `CharAgent/docs/DESIGN.md` #41（trace 标准：span 层级 run → turn → tool_call）与 #38（日志结构化 + 全链路关联）；`CharApp/docs/PLAN.md` §5 的 L3a 段（验收："CLI 能列出**每一次工具调用**"）

## 为什么这一片是整条 L3 的地基

L3a 的验收标准是"能回答**当时它看到了什么**"，而今天能答的粒度停在**帧级**：帧里有 `metadata.view`（这一轮发了什么）、`estimate_drift`、`cache_hit_ratio`，但**「这一轮调了哪个工具、参数是什么、返回了什么、花了多久」一个字都没有**。

L3b 的 HITL 也落在同一张表上：挂起态（`needs_approval`）、审批人、审批时刻都在 `charagent_tool_calls` 的列里。**两张嘴要的都是同一件事，只是现在没人往里写。**

## 现状（2026-09-24 核实）

**零件全在，一个都不缺 —— 缺的只是"谁来写"**：

| 零件 | 位置 | 状态 |
|------|------|------|
| 表 `charagent_tool_calls` | `db/schema.py:364-450` | 五张业务表之一，复合主键 `(run_id, message_id, tool_call_id)`，含 `needs_approval` / `approved_by` / `approved_at` |
| 实体 `ToolCall` | `db/entities.py:269-299` | 含 `duration_ms` / `result`（JSONB）/ `arguments`（原样 JSON 文本） |
| 状态枚举 `ToolCallStatus` | `db/entities.py:102-110` | 六值：`pending` / `running` / `succeeded` / `failed` / `cancelled` / `needs_approval` |
| 仓储 `ToolCallsRepository` | `db/repositories/tool_calls.py:73-253` | 方法齐：`add`(76) / `add_calls`(127) / `get`(158) / `list_for_run`(165) / **`set_status`**(182-223，**有 update**，带 `result` / `duration_ms` / `approved_by`) |
| **生产调用方** | —— | **零**。只有 `tests/test_db_store.py:337,372,390,414` 用它 |

`db/recorder.py` 现在只写 **threads / runs / messages** 三张表（`_write` 的四步：`_ensure_thread`(:438) → `runs.finish`(:439) → `messages.add_lines`(:450) → `threads.touch`(:455)）。ADR-0009 与 `PLAN.md:212` 都明写着「`charagent_tool_calls` 至今没有生产调用方」—— **本片就是来划掉那句话的**。

## 核心难点：外键链让"运行期间写一行"变成不可能

`charagent_tool_calls` 的 `message_id` 是**非空复合主键之一 + 外键指向 `charagent_messages`（CASCADE）**（`db/schema.py:374-380`，注释原文「**不能为空**」）。两个后果叠起来：

1. **写一行 tool_call 时，那条 assistant 消息行必须已经存在**；
2. 而 `messages` 行**只在运行收尾时才写**（`recorder._write` 在 `:450` 调 `add_lines`），**且 `message_id` 是插入时才生成的随机 `uuid4().hex`**（`db/repositories/messages.py:147`），**`add_lines` 的返回值还被丢弃了** —— 所以运行期间**没有任何地方拿得到**那条 assistant 的 `message_id`。

也就是说，**今天这个形状下，"运行中写 tool_call" 在类型上就做不到**。而 HITL 必须运行中写（挂起态要在挂起前落库，见 ADR-0014）。所以这一片真正要解的**不是"接个仓储"，是让这条链成立**。

### 三个修法

| # | 做法 | 代价 |
|---|------|------|
| **a** | **让 `message_id` 可推导**：`MessagesRepository` 生成 id 时不再用随机 uuid，改成由 `(run_id, turn, role)` 派生的确定值；运行中先落那条 assistant 行，收尾再写同一行时天然幂等（`ON CONFLICT DO NOTHING`） | 要动 id 生成（`add_lines` / `add_turn` 两处）；**老行仍是 uuid，两种形状并存**；必须有一条用例钉住"同一轮写两次不产生两行" |
| **b** | **messages 改成按轮增量写**：每轮结束就把那条 assistant 行落库，tool_call 行紧随其后；收尾的 `_write` 从"写全部"变成"补齐" | 更彻底（消息行在运行中就可见），但要重排 issue 22 建立的 `record` / `record_unfinished` 收尾结构，且"未完成的一轮"要重新定义 |
| **c** | 只在挂起时才写 tool_call 行，普通调用收尾一次写 | **不成立**：挂起那条同样撞 FK，没解决任何问题；而且"挂起"与"普通"两套写法必然漂移 |

**倾向 a**：它把"两次写同一行"变成天然幂等，不必拆 `_write` 的结构 —— 而 b 的重排风险落在一个刚被 issue 22/24 反复动过的区域。**但要说清：a 与 b 都要让 `MessagesRepository` 与收尾的 `_write` 能容忍"这一行已经存在"**，这是本片的真正工作量，不是顺手。

## 写入时机：先 `pending` 后终态

统一成两次写（与 issue 22 在 `runs` 上建立的 `begin` / `finish` 同构）：

```
工具执行前   INSERT  status = pending          （挂起时它先变成 needs_approval）
执行完成     UPDATE  status = succeeded/failed  + result + duration_ms
```

**为什么不给普通调用留"一次写终态"的快路**：挂起必须有中间态，两套写法会让"挂起调用"和"普通调用"走不同路径 —— 而普通路径才是主体，两套必然漂移。代价是多一次 UPDATE；每个 run 的工具调用只有几条到几十条，写入量翻倍无实质影响。

**为什么不是 `running`**：`ToolCallStatus` 有 `running`，但 `pending → running → 终态` 是三次写，而框架的工具执行是"一条消息里几个调用**并行**跑完一起回填"（`loop.py:370-398`）—— `running` 这一段在本框架里没有可观测的持续时间，写了也是自欺。**`pending → 终态` 两次**（`running` 留给将来真有异步长任务的场景）。

## 谁在什么时候写

`ConversationRecorder` 的**唯一调用方是 `ChatSession`**（`client/session.py:361` 的 `_begin_run`、`:381-386` 的 `_record`、`:379` 的 `_record_unfinished`）；`agent/loop.py` 与 `server/` **一处 recorder 调用都没有**（loop 只把 `run_id` 盖到帧上）。

所以接线点必须是 **`ChatSession` 与 `LoopResult` 之间**。`LoopResult.turns` 是 `TurnRecord` 的列表（`agent/utils/types.py:182-183`），`TurnRecord` 里有 `tool_names`（`loop.py:1015`）—— **有工具名，但没有参数、结果与耗时**。所以要么给 `TurnRecord` 补这三个字段，要么让 loop 通过一个回调交出去。

**这处留给实施定**：本片只钉住"数据必须从 loop 流到 recorder"，通道是 `LoopResult` 加字段还是注入回调，看哪个更贴合 `loop.py` 现有的形状。倾向**补 `TurnRecord`** —— 它与 `LoopResult` 一样是"这一段的产出"，不需要新增装配参数。

## 交付物

| # | 内容 |
|---|------|
| 1 | `MessagesRepository` 与 `_write` 能容忍重复行（`message_id` 可推导 / upsert），并有用例钉住 |
| 2 | `ConversationRecorder` 写入工具调用行：先 `pending`、后 `set_status` 终态（含 `result` / `duration_ms`） |
| 3 | 挂起那条调用的写入路径（`needs_approval`）—— **本片只写状态，不产生挂起**（产生挂起归 issue 34） |
| 4 | `TurnRecord` 补参数 / 结果 / 耗时（或等价的回调通道） |
| 5 | 框架侧用例：一次带工具调用的运行结束后，`list_for_run` 能列出每一次调用且字段齐全；**工具失败与"被拒绝"两种情况都要有一行**（它们同样是一次调用） |

## 验收

- [ ] 一次真实运行后，`charagent_tool_calls` 里**每一次工具调用一行**：`tool_name` / `arguments`（原样 JSON）/ `result` / `duration_ms` / `status` 全部有值
- [ ] 被护栏**拒绝**的调用也有一行（`status = failed`，`result` 是拒绝原因）—— 它是"模型想做什么"的证据，不能漏
- [ ] 参数是**原样 JSON 字符串**（`arguments` 列不做预解析，畸形 JSON 正是自纠错路径的信号——见 `db/schema.py` 那列的注释）
- [ ] 写入**不影响**现有的 1052 条用例；`runs` / `messages` / `threads` 三张表的既有行为一字不改
- [ ] 同一轮写两次不产生两行（那条幂等用例）
- [ ] `pytest -m pg_db`（真库）通过
- [ ] 无单价 / 无金额：本片**不碰** `total_cost`（那是 issue 28）

## 备注

- **它与 ADR-0016 是一对**：轨迹保留原文（订单号、地址、余额进 PG）是那条 ADR 的决定，本片是它的执行者。**别在本片里加脱敏** —— 那会当场推翻 ADR-0016。
- **注意 ADR-0006**：迁移历史已在 2026-09-23 压成一条（`0001_core`），本片**只动 `schema.py`，不加迁移**（`charagent_tool_calls` 表已经在了）。
- `tests/test_db_schema.py` 里的表结构断言（每列必须有 `comment=`）对本片不构成负担（表没变），但**加列就要加注释**那条纪律照旧。
- 与 issue 28 的分工：本片只管**把事实写进去**（谁、什么时候、什么参数、什么结果、多久）；"折成多少钱"是另一片。
