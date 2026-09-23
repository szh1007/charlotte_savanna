# 33 · 框架侧：`resume()` 记账（续跑段落脱离账本的问题）

**Status:** todo

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

- [ ] CLI `--resume` 之后，`charagent_runs` 多一行（**新**的一次运行），且它落的每一帧 `run_id` 指向那一行
- [ ] 传了 `run_id` 的 `resume()`：**不新增** `runs` 行，落下的帧 `run_id` 指向传入的那一行
- [ ] 传了 `run_id` 时，那一行的 `status` **没有被写成终态**（留给 issue 34 的恢复路径收）
- [ ] `messages` 行也补上（今天续跑段落在记录表里同样是空白 —— 与帧同一条链）
- [ ] 现有 1052 条用例不受影响；`ChatSession.ask()` 的记账行为一字不改
- [ ] 真机：CLI 里 `--resume` 跑一次，`trace`（issue 28）能查到这一段

## 备注

- **别改 `_seed_from_checkpoint` 去恢复 `run_id`**：那样 `--resume` 会静默沿用上一段的账，正是上表要分开的那两种语义。沿用必须是**显式传入**的。
- **与 issue 22 的关系**：22 把帧的 `run_id` 从 `loop_id` 里拆出来（`loop_id` = 一次循环执行，续跑沿用；`run_id` = 记录层一行账），本片正是那条拆分的完成态 —— `loop_id` 沿用是**结构事实**，`run_id` 沿用是**业务选择**。
- **与 issue 34 的分界**：本片只做到"续段记在哪一行"；"挂起时那一行的状态写什么"（`RunStatus.WAITING_USER`）归 34。`RunStatus.WAITING_USER` 已存在（`db/entities.py:78`，注释「挂起等人 (#25 HITL 审批)」）但**今天没有任何路径能走到它** —— `db/state.py` 的 `RUN_STATUS_FOR_OUTCOME`(:103) 键只来自 `LoopOutcome`（6 个值，`agent/utils/types.py:32-47`，**无挂起档**）。
