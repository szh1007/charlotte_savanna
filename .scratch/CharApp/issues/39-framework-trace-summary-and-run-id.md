# 39 · 框架侧：轨迹聚合入口 + CLI 打 run_id（L4 搭车两条）

**Status:** ready-for-agent

**Type:** task

**Blocked by:** 无（可与 40 / 41 并行开工）

**上游:** `CharApp/docs/PLAN.md` §5 的 L4 段；issue 38 §八第 1、2 条（本片是那两条的执行者）；issue 31 §4.2 的 19 号（CLI 打 run_id 的定案改法）

## 为什么有这一片

L4 的判据要读**每一次运行的工具轨迹**来算召回率与准确率（见 issue 44）。今天能做这件事的入口只有两个，都不够用：

| 入口 | 位置 | 缺什么 |
|---|---|---|
| `ToolCallsRepository.list_for_run(run_id)` | `db/repositories/tool_calls.py:186-201` | **只认单个 run**。聚合（按工具 × 时间段）得调用方自己写 |
| `python -m CharAgent.client.trace <run_id>` | `client/trace.py:114` / `:180` | 同上：一次只看一个运行，`--view` 是它的第二档，没有"看一片"的模式 |

第三条缺口是**拿不到 `run_id`**：框架 CLI 跑完只打回答，不打运行编号 —— issue 31 §4.2 的 19 号已经定了改法（"让 CLI 在完成行里打一次"），但没做。

**为什么不是"跑分器自己写一套"**：那样 CLI 与跑分器会各有一套聚合口径，两份代码迟早对不上。这一片的落点就是**一个查询面、两个调用方**（CLI 给人看，跑分器给程序读）。

## 现状（2026-09-28 核实）

- `ToolCallsRepository` 的公开方法只有 `add` / `add_calls` / `get` / `list_for_run` / `set_status` / `record_decision` / `list_pending_approvals`（`db/repositories/tool_calls.py:84/141/179/186/203/257/292`）。**没有按工具聚合、没有按时间段查询**。
- `list_for_run` 的排序是 `created_at, message_id, tool_call_id` 正序（`:186-201`），docstring 明写"轨迹断言（#62）与审计都从这里取"。
- `ChatSession` **知道本轮的 `run_id`**：它调 `recorder.begin(...)` 拿编号，再 `loop.run(..., run_id=run_id)`（`client/session.py` 的 ask 路径）。但它没把这个编号存下来给调用方读。
- `LoopResult` 是**循环层**的结果，**不认识记录层的 `run_id`** —— 这是框架自己的分层纪律，不要为了这一片给它加字段（issue 38 §八第 2 条已定案）。
- `ChatSession` 的 `prompt_ref` 走同一条思路（构造期存 `self._prompt_ref`，`client/session.py:226`）—— 本片照它的形状加 `last_run_id`。

## 本片要做的三件

### 一、仓储加聚合方法

在 `ToolCallsRepository` 上加一个聚合入口，形状要**同时**满足两种调用方：

- CLI：给我**最近一段时间**（`since` / `until`）的工具分布
- 跑分器：给我**这一批 run_id** 的工具分布

**一处必须现在定的实现选择：聚合在 SQL 侧还是 Python 侧？**

➡️ **Python 侧**。三条理由：

1. 本项目的量级是几十到几百行（L3b 收口时全库 18 条调用），远没到要 SQL `GROUP BY` 的程度
2. `FakeRecordSession.scalars` **不过滤 WHERE 也不排序**（`CharAgent/tests/doubles.py:340-352`，只有 `Message` 被过滤 `hidden`）—— 走 SQL 聚合的实现在假库上会**静默返回错数据**（跑分器用的正是假库，见 issue 41）
3. 拉回 Python 侧统计意味着这一片**不引入任何 SQL 方言差异**，真库假库行为一致

代价写明：数据涨到几万行时这条要改回 SQL 聚合。触发条件写进方法 docstring。

### 二、`client/trace.py` 加"看一片"的模式

`--summary`（配 `--since` / `--until` / 可选 `--tool NAME`），打出一张按工具分组的表：工具名 / 调用次数 / 成功数 / 失败数 / 挂起数 / 平均耗时。**它读的就是第一条那个聚合方法**，不另写一套。

现有形状保持不变：`trace <run_id>` 看单次、`--view` 看视图、新的 `--summary` 看一片。三档的互斥关系要在 `--help` 里说清。

### 三、`ChatSession.last_run_id` + CLI 完成行打编号

- `ChatSession` 加一个只读属性 `last_run_id`：最近一次 `ask()` / `resume()` 用过的运行编号，**没跑过时是 `None`**。照 `self._prompt_ref` 的既有形状存。
- 框架 CLI 的完成行打一次 `run_id`（形状：与现有完成行的字段风格一致，别新造格式）。

## 交付物

| # | 内容 |
|---|------|
| 1 | `db/repositories/tool_calls.py`：聚合方法（Python 侧统计 + docstring 写明量级边界） |
| 2 | `client/trace.py`：`--summary` 模式，与第 1 条共用查询面 |
| 3 | `client/session.py`：`last_run_id` 属性 |
| 4 | 框架 CLI：完成行打 `run_id` |
| 5 | 用例：聚合方法在**假库**与**真库**上给出同一份结果（这条专门防假库不过滤那个坑）；`last_run_id` 在 ask 之前是 `None`、之后是本次编号；恢复之后再 ask 会刷新它 |

## 验收

- [ ] CLI 跑一次问答，完成行打出 `run_id`（不再需要去库里捞编号）
- [ ] 拿那个编号 `trace <run_id>` 能看回来（老能力不回归）
- [ ] `trace --summary --since <iso>` 能按工具出分布
- [ ] **跑分器（issue 44）调用的是同一个聚合方法** —— 不是自己写的第二套
- [ ] 聚合方法在 `FakeRecordDatabase` 上给出与真库一致的数（用例钉住）

## 要定死的开放决策

| # | 决策 | 倾向 |
|---|------|------|
| 1 | 聚合方法的名字与签名（"按 run 集合"与"按时间段"怎么统一成一个入口） | 一个方法 + 参数二选一（要么给 `run_ids`，要么给 `since`/`until`），不要两个方法 |
| 2 | 挂起（`needs_approval`）在分布表里算成功还是单列 | **单列** —— 它既不是成功也不是失败，混进去会让 L4 的两组对比失真 |
| 3 | `last_run_id` 在 `resume()` 之后要不要刷新 | **要**（恢复段沿用同一编号，刷新是幂等的；不刷新会让"恢复后拿到的编号"变成上一轮的） |
