# 39 · 框架侧：轨迹聚合入口 + CLI 打 run_id（L4 搭车两条）

**Status:** done

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

- [x] CLI 跑一次问答，完成行打出 `run_id`（不再需要去库里捞编号）—— 真机 2026-09-28
- [x] 拿那个编号 `trace <run_id>` 能看回来（老能力不回归）
- [x] `trace --summary --since <iso>` 能按工具出分布
- [ ] **跑分器（issue 44）调用的是同一个聚合方法** —— 不是自己写的第二套
      → **本片验不了**（跑分器是 44 的活）。本片能做的是把它做成**可被调用的**：
      公开仓储方法 + 进三处门面；44 落地时照它调
- [x] 聚合方法在 `FakeRecordDatabase` 上给出与真库一致的数（用例钉住）

## 要定死的开放决策

| # | 决策 | 倾向 |
|---|------|------|
| 1 | 聚合方法的名字与签名（"按 run 集合"与"按时间段"怎么统一成一个入口） | 一个方法 + 参数二选一（要么给 `run_ids`，要么给 `since`/`until`），不要两个方法 |
| 2 | 挂起（`needs_approval`）在分布表里算成功还是单列 | **单列** —— 它既不是成功也不是失败，混进去会让 L4 的两组对比失真 |
| 3 | `last_run_id` 在 `resume()` 之后要不要刷新 | **要**（恢复段沿用同一编号，刷新是幂等的；不刷新会让"恢复后拿到的编号"变成上一轮的） |

## 实施记录（2026-09-28）

**四件全部落地**，三条真机验收逐条跑过。与票据原文有 **五处出入**，都是实施时定的：

| 票据原文 | 实际做法 | 为什么 |
|---|---|---|
| "参数二选一（要么给 `run_ids`，要么给 `since`/`until`）" | **可以叠加**（叠加时两个条件都满足） | 叠加是自然用法（"这批运行里、且在这个时间窗内"），而"不要两个方法"那条纪律照样守住了。叠加有用例钉 |
| `--until` 的语义没说 | 只写日期（`2026-09-28`）**补到当天最后一刻**；写了时刻的照那一刻算；不带时区按 UTC | 用户说"到 09-28"指的是含那一天，按零点算会把当天整个漏掉。help 里写明了 |
| 分布表的列（票面六个字段） | **多一列「其他」** | 六个字段加起来**不等于**总数：`pending` / `running` / `cancelled` 无处可去。少了它，表自己自相矛盾 |
| （未提） | `_table_lines` 从 `_calls_block` 抽出来共用 | 两处表格渲染同型；项目既有纪律明写"同一件事两处实现，迟早走偏"（`client/app.py:358`）。`_calls_block` 的行为由既有 12 条用例守着，一条没动 |
| （未提） | `--tool` 的过滤放在**读之后**；过滤后为空给专门一句话 | 「只看这一个」是显示上的事，不该改查询范围；而"这个工具没跑过"与"这段时间没有调用"是两回事 |

### 三条真机验收（2026-09-28，本机 Postgres + 真模型）

**先记一条现场**：动手时 `charagent_*` 三张表**都是 0 行**（此前清过库），于是空态先验了一遍
（"这段时间里一次调用都没有"）。随后：

| # | 怎么跑的 | 结果 |
|---|---------|------|
| 1 | `python -m CharAgent.client -q "现在几点"` | 完成行末多出 ` · 运行 398788c4b3614837919e2fac3c1241f8`（与"快照"那句并列） |
| 2 | `trace 398788c4b3614837919e2fac3c1241f8` | 老那一档原样打出来（账目 + 金额算式 + 工具调用表） |
| 3 | `--summary` / `--summary --since 2026-09-28` / `--since 2026-09-29` / `--tool get_current_time` / `--tool search_products` | 依次给出：一行分布 · 同一行带范围说明 · 空态 · 那一行 · "search_products 在这段时间里一次都没被调用" |

### 两轴复核（`/code-review`）后的修补

两个轴各一个子代理，审的是相对 `HEAD` 的工作区 diff。**八条采纳并改，三条判为不成立或保留**：

| 复核意见 | 处置 |
|---------|------|
| **标准轴（最重）**：范围判据写了两遍（SQL `where` + `_in_scope`），加一个条件要改三处 | **改**：`select` 不再带任何 `where`，筛选与统计都在 Python 侧一处（与"假库不过滤 WHERE"那条理由也更一致） |
| **标准轴**：`ToolCallSummary` 未进 `__all__`，逼得测试从 `client.trace` 反向导入 | **改**：进三处门面（`repositories` / `db` / **根门面**）—— 第三处是**门面测试**抓出来的（`test_root_facade.py`），复核只点出两处 |
| **标准轴**：`_NAMED_STATUSES` 与三个具名列各写一遍同一组状态 | **改**：改成 `(列名, 状态)` 成对声明，格子由它生成 |
| **标准轴**：`TraceOptions` 表达不了不变式 | **保留**：`parse_argv` 是唯一构造点，加 `__post_init__` 校验是给一个不存在的调用方付工程费 |
| **标准轴**：测试替身重复 | **不成立**：先查过 `tests/doubles.py` —— 里面**没有** recorder 替身，所以新替身不是"重复了现成的" |
| **规格轴**：票要求"三档的互斥要在 `--help` 里说清"，只有 `--summary` 那行写了 | **改**：description 说清三档，`run_id` 与 `--view` 两行各补互斥说明 |
| **规格轴**：交付物 #5 第三条"恢复之后再 ask 会刷新它"零用例 | **改**：新增 `CountingRecorder` + 一条用例。**复核点的坑是真的**：`ResumeRecorder` 恒交 `"run-new"`，拿它写这条是永远通过的空用例 |
| **规格轴**：`--tool` 的过滤与"命令行→仓储"这条接线零测试 | **改**：新增 4 条，走 `main(..., database=FakeRecordDatabase(...))` |
| **规格轴**：`render_summary` 那句 docstring 像"它真的又筛了一遍" | **改**：措辞改成"这是给人看的那行说明，不参与筛选" |
| **规格轴**：`cancelled` 被藏进「其他」 | **半改**：列不变（单列它会让表变八列，而 `pending`/`running` 仍要有个去处），但在 `_summary_cell` 的 docstring 里**写明它落在哪、以及为什么不单列** |
| **规格轴**：`--until` 补当天结束 / `_table_lines` 抽出共用属范围蔓延 | **保留**（理由见上面那张"出入"表），两条都记在这里不藏 |

### 一条顺带记下的既有现象（不是本片引入的）

`python -m CharAgent.client.trace`（不带参数）的用法错写 **stderr**，在 Git Bash 里是乱码 ——
`use_utf8_stdio()` 只切 stdin/stdout，而 `parser.error()` 走 stderr。**改动前后一个样**
（缺 run_id 那条路一直是它），所以不是本片带来的；真要修是"给 stderr 也切 UTF-8"，那是 `client/app.py` 的面。

### 跑过的用例（收尾那一遍）

| 命令 | 结果 |
|------|------|
| `pytest`（CharAgent 全量） | **1317 passed, 131 deselected**（基线 1284 / 130：本片 +28 unit、+1 pg_db） |
| `pytest CharApp -q` | **232 passed**（与基线逐字相同） |
| `pytest tests/test_db_tool_call_summary.py -m pg_db` | **1 passed**（真库与假库同结果） |
| `ruff check CharAgent/` + `ruff format --check` | All checks passed |

### 给 issue 44 的两句话

聚合入口**已经可以被调用了**（`ToolCallsRepository.summarize_by_tool`，进三处门面），跑分器落地时照它调即可 —— 那是本片验收第 4 条在本片内做不到的那一半。

**另有一处口径要对齐**：issue 41 写的"跑分器自己按 `run_id` 筛"说的是**单次运行的轨迹读回**
（`list_for_run`，假库不过滤 WHERE 所以要自己筛），与本片的**聚合**（一个查询面）不是一回事
—— 已在 issue 41 的交付物 #5 补了一句澄清。
