# 33 · 框架侧：`resume()` 记账（续跑段落脱离账本的问题）

**Status:** done

**Type:** task

**Blocked by:** issue 27（工具调用轨迹落库）—— 两片都动 `ConversationRecorder` 的写入路径，27 先落定形状

**上游:** `CharApp/docs/PLAN.md` §5 的 L3b 段第 2 条（「`resume()` 记账」）；issue 22 记下的开放项「`resume()` 不记账 → 续跑段落的帧 `run_id` 是 `None`，要不要给 resume 也开一行账……留给后面拍板」

## 为什么这一片是 HITL 的前置，不是一条可以再拖的欠账

**HITL 的恢复正是走 `resume()`。** 不修这条，恢复段落的每一帧 `run_id` 都是 `NULL`、没有 `runs` 行、没有 `messages` 行 —— 一次代付的"付款成功"那一段在记录层是**空白的**：前端刷新看不到、成本记账记不上、轨迹串不起来。ADR-0014 定的那条判据（`charagent_tool_calls.status = needs_approval AND approved_at IS NULL`）也要求挂起行的 `run_id` 有个能 join 的落点。

所以它在 PLAN §5 里的措辞是"**从欠账升级为前置**"。

## 现状（2026-09-24 核实）

**`ChatSession.resume()` 完全不碰记录层**（`client/session.py:389-414`）：

```python
checkpoint = await self._saver.load_latest(thread_id)   # :408
if checkpoint is None: return None
return await self._run(self._loop.resume(self._restore(checkpoint)))   # :413
```

对比 `ask()` 那边：`:361` 调 `_begin_run`（→ `recorder.begin`）、`:379` 调 `_record_unfinished`、`:381-386` 调 `_record`。`resume()` 一条都没有。它的 `_run`（`:456-479`）只更新 `_history` / `_summary` / `_summary_covers` / `_parent_id`。

**`run_id` 在 loop 里也不会自己长出来**：`LoopState.run_id` 的**唯一赋值点是 `loop.py:573`**（`_run` 构造 `LoopState` 时），而 `_seed_from_checkpoint`（`:1029-1057`）接的是 `turn_count` / `total_tokens` / 五个分量 / `truncation_count` / `content_parts` / `summary` / `prompt_ref` / `last_checkpoint_id` —— **`run_id` 不在其中**。落帧时 `_save_checkpoint` 用 `run_id=state.run_id`（`:1151`），于是续跑落的帧 `run_id` 全是 `None`。

**结论：一次 `--resume` 之后的所有帧，在记录层都是孤儿。**

## 核心设计：两种 resume 不是一回事

这是本片最容易做错的一处 —— 把它当一件事，会得到一个错的口径。

| | 谁在用 | 语义 | 账目 |
|---|---|---|---|
| **CLI `--resume`** | `client/app.py:698-749` 把 `--resume` 排在最前，`_run_once` 里调 `session.resume()` | 「这段会话我接着聊」 | **新的一次运行** —— 它是新的一段「从提问到答复终止」，只是从旧快照接着跑 |
| **HITL 恢复** | issue 34 的新端点 | 「**同一次运行**的第二段」 | **沿用原来那一行账**（`loop_id` 与 `run_id` 都不变，见 `CONTEXT.md` 的「恢复」词条） |

### 做法

**`ChatSession.resume()` 加一个可选的 `run_id` 参数**：

```python
async def resume(self, *, run_id: str | None = None) -> LoopResult | None
```

- **不传** → 与 `ask()` 同构地开一行新账（`_begin_run` → 跑 → `_record`）
- **传了** → 这是同一次运行的续段：`runs` 行**不新建**，`LoopState.run_id` 直接用它；收尾**不改**那一行的终态（挂起这一次还没结束，终态由 issue 34 的恢复路径收）

传下去的路径：`ChatSession.resume` → `_loop.resume(checkpoint, run_id=...)` → `_run(..., run_id=...)`（未传时 `_run` 内部仍按今天的方式生成）。

**为什么是参数而不是"`resume()` 一律沿用"**：CLI 那条如果也沿用，`--resume` 一次会在同一天里往同一行账上叠三段互不相干的对话，"这次运行花了多少"当场失去意义。**判据是"是不是同一次运行的第二段"**，只有 HITL 知道答案，所以由调用方给。

## 交付物

| # | 内容 |
|---|------|
| 1 | `ChatSession.resume()` 支持 `run_id`；不传时开新账（走 `_begin_run` / `_record`） |
| 2 | `agent/loop.py` 的 `resume()` / `_run` 接 `run_id`；`_seed_from_checkpoint` **仍不**恢复 run_id（沿用是调用方的选择，不是快照的属性） |
| 3 | 传了 `run_id` 时，收尾**不调** `runs.finish`（那一段还没完） |
| 4 | 用例：`--resume` 那条路（不传）落下的帧 `run_id` **不再是 None**；传了 `run_id` 的那条路落下的帧与原来的 run 行对得上 |

## 验收

- [x] CLI `--resume` 之后，`charagent_runs` 多一行（**新**的一次运行），且它落的每一帧 `run_id` 指向那一行
- [x] 传了 `run_id` 的 `resume()`：**不新增** `runs` 行，落下的帧 `run_id` 指向传入的那一行
- [x] 传了 `run_id` 时，那一行的 `status` **没有被写成终态**（留给 issue 34 的恢复路径收）
- [x] `messages` 行也补上（今天续跑段落在记录表里同样是空白 —— 与帧同一条链）
- [x] 现有 1052 条用例不受影响；`ChatSession.ask()` 的记账行为一字不改
- [x] 真机：CLI 里 `--resume` 跑一次，`trace`（issue 28）能查到这一段

## 实施记录（2026-09-25）

### 交付物四条全部落地

| # | 内容 | 落在哪 |
|---|------|--------|
| 1 | `ChatSession.resume(*, run_id=None)`；不传时开新账 | `client/session.py`：`_begin_run` →（跑）→ `_record`，与 `ask` 同一条链 |
| 2 | loop 的 `resume` / `_run` 收 `run_id` | **issue 22 已经做好**，本片只补了 `AgentLoop.resume` 缺失的那条 Args 说明；`_seed_from_checkpoint` 照旧**不**恢复 run_id |
| 3 | 传了 `run_id` 时不调 `runs.finish` | 记录层新增 `finish_run` 开关（`RunRecorder.record` / `record_unfinished`），关掉时**运行行一笔不碰**，其余五步照走 |
| 4 | 用例 | 离线 3 条（`test_client_session.py`）+ 真库 2 条（新文件 `tests/test_client_resume_db.py`）+ 记录员 2 条（`test_db_recorder.py`） |

**除了票据点名的四件，还补了两处**（都是「不做就会留下假数据」，不是顺手加的）：

- **`record_unfinished` 的 `question` 变成可选**（`str | None`，**必填** —— 不许漏传）：续跑段不是提问触发的，而失败那一轮也要落一条「这一轮没答完」。不补的话，命令行 `--resume` 一旦失败，**它自己刚建的那一行运行会永远停在 `running`**。**不编那句提问**：编一条 `user` 行会让记录撒谎（「只有真由用户输入产生的消息才是 user」是这一层立着的硬规矩）。
- **`RunSettlement`（收尾那一笔）打包**：状态 / 账目 / 金额 / 最后一帧这四样必须同进同出，打包之后「这一段不收尾」也只要说一次（传 None）。`_write` 的签名因此从 11 个参数降到 8 个。

### 真机（2026-09-25，本机 Postgres + 真模型 deepseek-flash）

**会话 `cli-resume-33b`**：先问一句（`-q`），再单独跑一次 `--resume`（stdin 关掉，交互模式读到 EOF 自行退出）。

| | 运行行 | 轮数 / token | 帧 | 消息行 |
|---|---|---|---|---|
| 第一段 | `077e55b7…` `finished` | 1 / 1844 | 第 1 轮 → 指着 `077e55b7…` | user + assistant |
| **续跑段** | `ebbed262…` `finished` | **2 / 3735**（累计口径） | 第 2 轮 → 指着 `ebbed262…` | **assistant（从前这一段一条都没有）** |

- **多了一行**：2 行运行行，续跑段是**新的一次运行**（编号不同）✓
- **帧不再是孤儿**：两帧各有归属（改之前续跑落的那一帧 `run_id` 是 `None`）✓
- **消息补上了**：续跑段的答复落在它自己那一行下面 ✓
- **`trace` 查得到**：`python -m CharAgent.client.trace ebbed2620530419fa4401603e28bd10f` 打出 `状态 finished · 累计 3735 token · 金额 ¥0.001489 (valley)` —— 一次运行的账在「两段」之后仍然是一条完整的账 ✓
- 另有一个会话 `cli-resume-33-1790321329`（`--resume` 与一句提问连着跑的形态，3 行运行行 / 3 帧 / 5 条消息）—— 两趟都留着，可以自己用 psql 复盘；要清就 `DELETE FROM charagent_threads WHERE thread_id LIKE 'cli-resume-33%'`（运行行 / 消息 / 帧跟着 CASCADE 走）。

**传了 `run_id` 的那条路没法在命令行上跑**（它由 HITL 的恢复端点触发，那是 issue 34），所以那一条的真库证据来自 `tests/test_client_resume_db.py`（真库、独立 schema）：手工建一行「还开着」的运行 → 传它的编号续跑 → 断言**没有新增行**、那一行还是 `running`、`finished_at` 空着、`turn_count` 还是 0，而帧与消息都指回它。

### 用例（新增 7 条）

| 文件 | 断的是什么 |
|------|-----------|
| `tests/test_client_session.py`（3 条，离线） | `--resume` 开新账并收尾（`begin` 调了、`finish_run=True`、`since` **就是**交给 loop 的那份历史的长度）· 传 `run_id` 时不新开账不结账（`finish_run=False`）· 续跑失败记的是「没答完」且 **question 是 None** |
| `tests/test_client_resume_db.py`（2 条，真库） | 上面那两条验收在真库里的样子（运行行 / 帧 / 消息三种行一起看） |
| `tests/test_db_recorder.py`（2 条，离线） | `finish_run=False` 时运行行一笔不碰（而消息照写）· `question=None` 时只写那句说明 |

**`since` 这条为什么要专门断**：记录层按 `(run_id, 下标)` 算消息编号，运行中途那一拍（`flush`）用的是 `flushed = len(messages)`，收尾这一拍必须用**同一个基准** —— 对不上的话，同一条消息会写出**两行**。

**两条真验过（把实现改坏，看用例红不红）**：

| 临时改坏 | 结果 |
|---------|------|
| 记录员忽略 `finish_run`（照样结账） | 那条用例红：`- running` / `+ finished` |
| 会话不把 `run_id` 交给 loop（改回从前） | 两条真库用例红，报的正是本片要修的症状：帧的 `run_id` 是 `None` |

### 两轴复核（`/code-review`）后的修补

- **一个开关三种写法**（`continuation` → `not continuation` → `not finish_run`）：改成开头一句 `finish_run = run_id is None`，少一层否定。
- **`question` 从「有默认值」改成「必填、可为 None」**：漏传与「故意没有」不该长得一样（对齐 `_write(run_id: str | None, ...)` 的既有写法）。
- **`record` 无条件构造 `facts` 再丢掉**：改成 `if finish_run:` 里的整块，顺带把金额那一段的条件表达式挪进块里。
- 文档三处：`test_client_session.py` 的场景索引补上本片的条目 · `session.py` 里「见 `resume` 的那张表」（那张表已经改成列表）· `loop.py` 的 Args 补 `run_id`。
- **复核照出来一条 latent，已按票据的要求定下来**：`db/recorder.py` 的 `_write_calls` 那段注释原本写着「若补做的那条调用，发起它的 assistant 行不属于本次 run，外键会拦下……**那条路由 issue 33 定**」。**本片的决定是它列的第一条**：HITL 的续跑沿用挂起那次运行的编号（本来就是同一次运行），于是补做的调用直接寻址第一段写下的那一行。剩下那半边（**命令行从挂起点续跑**）今天**到不了** —— 挂起点只由 HITL 产生（issue 34），而命令行那条是「接着上次聊」。修法已经写在那段注释里（按帧里的 `run_id` 反查那条 assistant 行，或按本次运行补写一行），留给真需要它的那一天。

### 与 issue 34 的分界（守住）

没有 `WAITING_USER`、没有 `Suspension`、没有 `Decision` / `LoopOutcome` 的改动；「挂起时那一行写什么」仍然归 34。本片只保证：**续跑段不碰那一行**，并且把「谁收尾」这个开关交出去。

## 改了哪些文件

| 文件 | 改了什么 |
|------|---------|
| `CharAgent/client/session.py` | `resume(*, run_id=None)`：开账 / 收尾 / 失败三拍接上记录层；`_record` / `_record_unfinished` 多一个 `finish_run`，后者的 `question` 可为 None |
| `CharAgent/db/recorder.py` | `RunRecorder` 两个收尾入口的 `finish_run`；`question` 可选；`RunSettlement` 打包 + `_write` 只调一次 `runs.finish`；`_write_calls` 那段注释记下本片的决定 |
| `CharAgent/agent/loop.py` | 只补 docstring：`resume` 的 `run_id` 那条 Args（代码在 issue 22 就位） |
| `CharAgent/tests/test_client_session.py` | 新增 3 条离线用例 + 共用的记录员替身 `ResumeRecorder`；场景索引补条目；5 个既有替身补 `finish_run` |
| `CharAgent/tests/test_client_resume_db.py` | **新文件**：2 条真库用例（验收的前两条 + 第三条） |
| `CharAgent/tests/test_db_recorder.py` | 新增 2 条：不结账时运行行不动 · 无提问时不编 user 行 |

**没动的**：`checkpoint/`（恢复机制一行没改，票据备注点名不用动）、`ask()` 的记账路径（`finish_run` 默认 True，行为逐字不变）、`ChatSession` 的公开三动作签名（只有 `resume` 多了一个可选参数）。

## 备注

- **别改 `_seed_from_checkpoint` 去恢复 `run_id`**：那样 `--resume` 会静默沿用上一段的账，正是上表要分开的那两种语义。沿用必须是**显式传入**的。
- **与 issue 22 的关系**：22 把帧的 `run_id` 从 `loop_id` 里拆出来（`loop_id` = 一次循环执行，续跑沿用；`run_id` = 记录层一行账），本片正是那条拆分的完成态 —— `loop_id` 沿用是**结构事实**，`run_id` 沿用是**业务选择**。
- **与 issue 34 的分界**：本片只做到"续段记在哪一行"；"挂起时那一行的状态写什么"（`RunStatus.WAITING_USER`）归 34。`RunStatus.WAITING_USER` 已存在（`db/entities.py:78`，注释「挂起等人 (#25 HITL 审批)」）但**今天没有任何路径能走到它** —— `db/state.py` 的 `RUN_STATUS_FOR_OUTCOME`(:103) 键只来自 `LoopOutcome`（6 个值，`agent/utils/types.py:32-47`，**无挂起档**）。
