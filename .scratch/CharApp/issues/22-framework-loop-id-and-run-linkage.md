# 22 · 框架侧：帧与运行的双向溯源（`loop_id` 改名 + 外键 + 这一轮发出去的视图入帧）

**Status:** done

**Type:** task

**Blocked by:** 无（issue 18 的真机查库查出来的遗留，但与本片独立）

**上游:** `CharAgent/docs/DESIGN.md` #5（快照 / 回溯）、#12（记录与水合）、#25（HITL 挂起）；`CharApp/docs/PLAN.md` §5（L3 可观测：能回答「当时它看到了什么、花了多少」）

## 做什么

起因是 2026-09-23 用户在真机上查库时发现：**`charagent_checkpoints.run_id` 与 `charagent_runs.run_id` 一个都对不上**（两段验收会话实测：两列各自 9 / 8 个 uuid，交集为空）。查下来是两个不同层次的编号**撞了名**：

| 层 | 谁生成 | 语义 |
|---|---|---|
| 快照层（帧上那个） | `agent/loop.py` 的 `uuid4().hex`，resume 沿用 | 「这一帧是哪一次**循环执行**落下的」 |
| 记录层（运行行那个） | `db/repositories/runs.py` 的 `uuid4().hex` | 「这是哪一行**账**」= `charagent_runs` 主键，`messages` / `tool_calls` 的外键都指向它 |

名字一样、意思两样，而且**两边没有任何一列能把它们连起来**。本片一次说清并接上：

1. **帧上的 `run_id` 更名 `loop_id`** —— 名字贴合它「loop 执行过程中产生的编号」这个含义。**含存储格式**（帧记录里的 JSON 键一起改，schema v5 → v6）
2. **帧新增 `run_id` 列**（外键 → `charagent_runs.run_id`）—— 这一帧属于哪一行账
3. **`charagent_runs` 新增 `last_checkpoint_id` 列**（外键 → `charagent_checkpoints.checkpoint_id`）—— 这一行账落的**最后一帧**，顺 `parent_id` 往回走就是本次运行落的全部帧
4. **帧记下这一轮的压缩结果（`compiled`）** —— 用户追加（2026-09-23）：`_compile_view` 每一轮模型调用前都会产出一份 `CompiledView`，而它今天**用完即弃**（只把 messages 交给 generate、把 summary / covers 写回 state、把计数发进事件）。要让「当时它看到了什么」真的可查，这一份得跟着那一帧落库

三件之后的查询是直线，不用猜时间戳：

```
runs 那一行 ──last_checkpoint_id──▶ 帧 ──parent_id 往回──▶ 本次运行的每一帧
      ▲                                    │
      └──────────── checkpoints.run_id ────┘
```

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| 帧的编号由 loop 生成：新 run `uuid4().hex`，**resume 沿用帧里那个**（「同一个 run 接着跑」），resume 之外没有第二处读它 | `agent/loop.py:550`、`:531`、`:1057` |
| `Checkpoint.run_id` 字段 + `create(run_id=)` + 校验名 `"run_id"` | `checkpoint/utils/types.py:258`、`:271`、`:304` |
| 存储键：**Redis 的整条记录里有** `"run_id"`（`encode_record` / `decode_record`），**PG 的 body 里没有**（它走表列） | `checkpoint/serialization.py:313-326`、`:356`；`checkpoint/postgres.py:133`、`:271` |
| 当前 `SCHEMA_VERSION = 5`，迁移注册表 `MIGRATIONS[1..4]`，版本历史写在 docstring 里 | `checkpoint/utils/types.py:41`、`checkpoint/utils/migrations.py:201-204` |
| 帧表列：`checkpoint_id`(PK) / `thread_id` / **`run_id`** / `turn_number` / `schema_version` / `state` / `metadata` / `parent_id`(自引用 FK) / `created_at` | `db/schema.py:415-475` |
| saver 自建表用的**是同一份 metadata**（`create_tables([checkpoints])`） | `checkpoint/postgres.py:238` |
| 运行表的写口三条：`add`（建 `created` 行，可显式给编号）、`add_terminal`（一次落终态行）、`try_transition`（乐观锁推进状态） | `db/repositories/runs.py:32`、`:99`、`:203` |
| **仓储层文档里的推荐用法就是「先 `add` 再 `try_transition`」**（记录员抄了近路，走了 `add_terminal`） | `db/__init__.py:35-45`；`db/recorder.py:355` |
| 记录员两条入口 `record(...)` / `record_unfinished(...)`，会话在运行收尾处调它们 | `db/recorder.py:175`、`:202`；`client/session.py:534-565` |
| `LoopResult.last_checkpoint_id` **已经交出来了**（「这一段 run 落的最后一帧」），差的是没人往库里写 | `agent/loop.py:633`、`agent/utils/types.py:183`、`:207` |
| `messages.run_id` / `tool_calls.run_id` 已经是 → `charagent_runs.run_id` 的外键（记录层内部本来就 join 得上） | `db/schema.py:278`、`:334` |
| 用户已清空库里那几张表的数据 → **不需要数据迁移**，但**老格式帧仍要读得回来**（v5 fixture 与「向前兼容」那条纪律不准破） | 用户 2026-09-23 |
| `_compile_view` 在**每次模型调用前**跑一次（`_decide` 里那一行），一轮一份；它今天**只返回 messages**，`CompiledView` 的计数（dropped / truncated / estimated / saved / summarized / warning）用完即弃 | `agent/loop.py:683`、`:720-737`（`-> list[ModelMessage]`） |
| 帧的两块内容分工（v3 起）：**`state` = 进度**（恢复才用：消息历史 + 计数器 + 挂起点），**`metadata` = 观察值**（给人看、回放调试用：来源 / 本轮 token 与耗时 / 工具 / 结束原因） | `checkpoint/utils/types.py` 的 `CheckpointState` / `CheckpointMetadata` docstring；`db/schema.py:438-450` 的列注释 |
| 帧的 metadata 由 `_turn_metadata(...)` 在 `_record_turn` 里造 | `agent/loop.py:894-901`、`:912` |
| 「压缩这一次做了什么」已经有现成的载荷形状：`context_compacted_data(compiled, turn=…)`（dropped / truncated / estimated_tokens / saved_tokens / summarized / warning） | `agent/utils/events.py:102-125` |
| 挂起补做那一轮（`source=suspension`）**没有模型调用**，因此没有本轮的 `compiled` —— 别把上一轮那份顺手写进去 | `agent/loop.py:576-586`、`:861` |

## 具体任务

### 1. `run_id` → `loop_id`（代码 + 存储键 + 库列）

- `Checkpoint.run_id` → `loop_id`（字段、`create(loop_id=)`、`check_identifier("loop_id", …)`）
- `AgentLoop.run(run_id=)` / `resume(run_id=)` 参数名 → `loop_id=`（它喂的就是这个字段；resume 那条是 `loop_id or checkpoint.loop_id`）
- 序列化：`encode_record` / `decode_record` 的键 `"run_id"` → `"loop_id"`
- `SCHEMA_VERSION` 5 → 6；`MIGRATIONS[5] = _v5_to_v6`：**只做「键在就改」的幂等改名**（PG 的 body 里本来没有这个键，Redis 的整条记录里有 —— 同一个函数要两边都吃得下）；版本历史补一行
- 库列名：`db/schema.py:425` 的 `run_id` → `loop_id`；alembic 一条 `alter_column(new_column_name="loop_id")`

### 2. 帧 → 运行的外键（B1：编号在运行开始前定下来）

- 新列 `checkpoints.run_id`：`String(128)`，`ForeignKey("charagent_runs.run_id", ondelete="SET NULL")`，**可空**。可空是语义，不是偷懒：没有记录层的进程（框架 CLI 演示）、建行失败、老帧 —— 三种都该是 NULL，而不是「指向一个不存在的行」
- `Checkpoint.run_id: str | None = None`（记录层编号）+ `create(run_id=)`；Redis 的整条记录里也带上它（PG 走列），`decode_record` 缺这个键时给 None（老帧）
- **B1 的时序**：帧是在运行**中途**逐轮落盘的，而运行行今天在运行**结束**才建 —— 所以编号必须提前。记录员加一条 `begin(thread_id) -> str | None`：用 `runs.add()` 建一条 `created` 行并返回编号（None = 没配记录层或库不可用，这一轮不记账）
- 会话把编号交给 loop（`run(..., run_id=<账目行编号>)`）盖到每帧上；收尾时 `record(run_id=…, …)` / `record_unfinished(run_id=…, …)` **推进那一行**，而不是新建
- 记录员的终态写入从 `add_terminal(…)` 改成「按 run_id 更新那一行」（状态 + 账目字段 + `finished_at` + `last_checkpoint_id`）。实现时二选一：给 `add_terminal` 加显式 `run_id` 并让它走 UPDATE，或在仓储里补一个 `finish(...)`；**别新造两套终态写法**
- 降级：`begin` 失败 → 这一轮不记账（走既有的 `_missed` + 下次补提示行那条路，**不新增机制**）
- saver 自建表要连 `runs` 一起建（`create_tables([runs, checkpoints])`）—— 否则新外键在「没跑迁移」的环境里建不出来（PG 要求被引用表先存在）
- **悬挂的 `created` / `running` 行**：进程被杀会留下（今天什么都不留，所以是**多**出来的）。先接受、记进开放项；「同一会话开新运行时收掉陈旧行」这类兜底**默认不做**

### 3. 运行 → 帧的溯源列

- `runs.last_checkpoint_id`：`String(128)`，`ForeignKey("charagent_checkpoints.checkpoint_id", ondelete="SET NULL")`，可空
- 记录员在收尾那一笔里把 `LoopResult.last_checkpoint_id` 写进去

### 4. 这一轮的压缩结果入帧（`compiled`）

- 运行期：`_compile_view` 除了返回 messages，**把那份 `CompiledView` 存进 `LoopState`**（新字段 `state.compiled`）；没配 compactor 时显式置 None —— 与「每轮只认本轮那份」配套，挂起补做那轮因此自然是 None，不会把上一轮那份重复写进去
- 落盘：`_turn_metadata` 把它塞进帧的 **`metadata`**（不是 `state`）——
  - **为什么是 metadata**：v3 特意把「观察值」从 state 拆出去过；这一份是回放/审计用的（续跑不需要它，续跑会重新投影），塞进 state 等于又把两块搅在一起；`summary` / `summary_covers`（真正要续跑的那两样）已经在 state 里了
  - **不是一个新列**：`metadata` 本身就是 JSONB，加一个键不需要 DDL；帧的三大块（state / metadata / 表列）不该再多长一块出来
- 键的形状**复用现成的**：`context_compacted_data(...)` 那套计数（dropped / truncated / estimated_tokens / saved_tokens / summarized / warning）+ 一个 **`messages`**（这一轮真的发出去的那份视图）
- **`messages` 只在「视图 ≠ 账本」时才存**；相等时给 `null`，语义写死在注释里：**「视图就是账本本身」**。理由：相等时视图是账本的副本，帧里本来就有全量账本（`state.messages`），再存一份等于把每一帧的体积翻倍；而审计价值全在「视图真的不一样的那些轮」
  - **判据必须是"直接比"，不能图省事看 `compacted`**：今天两者恰好等价（不切就不投影），但那是下面 issue 23 那条缺陷的副产品 —— 一旦「压过之后视图一直带摘要」被修好，`compacted=False` 的轮也会有大不相同的视图，用 `compacted` 做判据会把它们全漏掉（2026-09-23 用户当场指出）
- 不重复存的：`summary` / `summary_covers`（state 里有）、`summarizer_usage`（已经累进运行的用量分量与账目行）
- 配套：`CheckpointMetadata` 加字段 + `serialization` 的 metadata 载荷读写 + v6 迁移给老帧补 None（**与第 1 件同一版，不额外再加一版**）

### 5. 测试

- 框架：改名后的往返（`encode_record` / `decode_record`）；**v5 帧读回来 → 键名已变且数据没丢**；帧带 / 不带 `run_id` 的往返；resume 沿用 `loop_id`
- 框架（`compiled`）：压过的轮 → 帧里 `metadata.view.messages` **就是当时 generate 收到的那份**（与模型替身记录的请求逐条比）；没压的轮 → `messages` 为 null；挂起补做那轮 → 整份 view 为 None；读写往返（含 v5 老帧补 None）
- db 层（标记 `pg_db`）：`begin → 跑（落帧）→ record` 之后三处对得上（帧的 `run_id` = 那一行的 `run_id`；运行行的 `last_checkpoint_id` = 最后一帧；顺 `parent_id` 能列出本次运行的全部帧）；外键的 `ON DELETE SET NULL` 行为
- 业务侧：`FakeRecordDatabase`（`CharAgent/tests/doubles.py`）跟着改成两段式
- **真机**：跑一段新会话，查三列互相对上（把 SQL 与结果贴进记录），并顺 `parent_id` 列出本次运行落的帧

## 验收

- [x] 全仓 `checkpoint/` 里不再有 `run_id` 这个字段名（`grep -rn "\.run_id" CharAgent/checkpoint/` 只剩新加的那一列），帧记录里的 JSON 键也是 `loop_id`
- [x] `SCHEMA_VERSION` = 6，版本历史有 v6 一行，**v5 帧仍然读得回来**（有 fixture 与用例）
- [x] `charagent_checkpoints.run_id` 是外键，且**写时就有值**（不是事后回填）—— 指 `ask` 那条路；`resume()` 不开账，它续跑段落的帧是 NULL（见开放项 1）
- [x] 帧**只插不改**：新列之外没有任何 UPDATE 打在 `charagent_checkpoints` 上
- [x] `charagent_runs.last_checkpoint_id` 有值，且顺 `parent_id` 能回溯出本次运行落的每一帧
- [x] 帧里读得到「这一轮发出去的视图」：**视图 ≠ 账本**的轮有 `metadata.view.messages`（与模型收到的逐条一致），相等的轮是 `null`（= 视图就是账本），挂起补做那轮整份为 None
- [x] 帧体积没有因此翻倍：没压的轮不存视图消息（长会话里可量一次）
- [x] 真机跑一段：三列对得上（贴查询与结果），并贴一帧的 `metadata.view`（压过的轮）
- [x] 全量测试与 `ruff` 干净（含 `-m "pg or pg_db or redis"`）

## 备注

- **为什么不是「删掉帧上那个编号」**：resume 的沿用语义（「同一个 run 接着跑」）是 HITL #25 挂起-恢复的骨架，删了它 #25 得重新发明。改名而不是删除，正是为了让它跟运行行的编号不再混为一谈
- **为什么帧也要指向运行行**（而不是只留 `runs.last_checkpoint_id` 一头）：两个方向都能查最省事；只留一个方向的话，「某帧属于哪一行账」得顺链回溯 + 判边界（续跑时 `loop_id` 会跨段复用，边界判不准）
- **为什么选 B1 而不是事后回填**：B1 是仓储层原设计的那条路（`db/__init__.py` 自己写着），两列都写时定稿、外键成立、帧保持只插不改；事后回填那条要 UPDATE 帧，且续跑段的边界只能靠时间戳猜
- **第 4 件里两个可翻的取舍**（都写在任务 4 的理由里）：① 落在 `metadata` 而不是 `state`（v3 的分工，续跑不需要它）；② 视图消息**只在「视图 ≠ 账本」时存**（相等时视图就是账本的副本，帧里已有全量账本，再存一份会让每帧体积翻倍）。若日后要「每帧都能直接读出发出去的那份」，把 ② 翻成「总是存」即可 —— 代价就是那点体积
- **判据为什么不能图省事用 `compacted`**：今天两者恰好等价（不切就不投影），而那是 **issue 23** 那条缺陷的副产品 —— 那条修好之后，`compacted=False` 的轮也会有大不相同的视图（带摘要），用 `compacted` 判会全漏掉。**先按"直接比"写，别把缺陷的形状固化进新代码**
- 与 issue 19 / 20（前端左栏与会话管理）无关；它让 L3 那句「能回答当时它看到了什么、花了多少」第一次可以 join
- 真机数据已由用户清空，所以**不写数据修复迁移**；但老格式帧的可读性照旧保留（那是契约，不因本机清库而放弃）


---

## 实际开发情况 2026-09-23

### 一、实现时才定下来的几处（票里给了方向，细节在这里）

1. **`begin` 建行直接给 `RUNNING`，不是 `CREATED`**：状态机里 `CREATED` 到得了 `FAILED` / `CANCELLED`，**到不了 `FINISHED`**（`db/state.py` 的合法迁移表）—— 写成 `CREATED` 就得为「跑完」多补一次没意义的中间推进. 建行那一刻这次运行真的开始了（调用方紧接着就调 loop），`RUNNING` 是如实描述.
2. **`add_terminal` 被 `finish` 取代**（不是并存）：B1 之后「一次 INSERT 落成终态」在记录员那条路上没有调用方了，留两个终态写入口只会让人分不清该用哪个. `finish(run_id, *, status, …, last_checkpoint_id=None) -> bool` 是一条带 WHERE 的 UPDATE，状态校验走 `ensure_transition(RUNNING, …)`；三条 pg_db 用例跟着改名改写.
3. **`_ensure_thread` 收「标题字符串」而不是「那批行」**，并且**会补空标题**（新增 `ThreadsRepository.set_title`）：`begin` 那一拍**通常带着标题**（`_begin_run(question)`），但 protocol 里 `title` 的默认值是空串 —— 不带时先建一个空标题的会话行，收尾那拍用首条用户消息补上（不补的话，那段会话在前端左栏永远没名字）. `set_title` 带 `WHERE title = ''`，已有标题不覆盖（标题的语义是「首条用户消息」，不该漂）.
4. **`LoopState.view` 存的是「装好的观察值」，不是 `CompiledView` 对象**：`messages` 要不要带，取决于「视图与账本是否相同」，而这个判断**必须在投影那一刻做** —— 落帧发生在轮末，那时账本已经长了一条（模型这轮的答复），再比就会永远判成「不一样」（第一次实现就踩了，用例当场红）. 判断随之收进 `compaction.view_payload(compiled, ledger)`，由 `_compile_view` 调用.
5. **两张表之间的环用 `use_alter=True` 破**：`checkpoints.run_id ⇄ runs.last_checkpoint_id` 互为外键，SQLAlchemy 排不出建表顺序 —— 不标 `use_alter` 的话它只报一条 `unresolvable cycles` 警告然后**跳过那条约束**（库里少一个外键，而没有任何地方会红）. 标在 `runs.last_checkpoint_id` 上（迁移里本来就是两条 ALTER，不受影响）.
6. **迁移里的外键名写死**（`fk_<表>_<列>_<被引用表>`）：手写迁移不写死的话，建出来的名字与 metadata 那边对不上 —— `test_db_alembic` 的 `compare_metadata` 会当成「代码与库不一致」而红（实测过一次）.
7. **假库（`tests/doubles.py`）学会落 UPDATE**：B1 把「插终态行」换成了「推进那一行」，而假库原来对非 INSERT 语句只记一笔表名. 现在它按 `_values` / `_where_criteria` 改内存里的行（非公开属性，注释里写了为什么：公开的 `compile().params` 把 SET 与 WHERE 混在一起）. 顺带两处语义调整：
   - `rows_of(table)` 改读**实体的当前状态**（原来读插入流水）—— 断言「最终写成了什么」需要它；
   - 新增 `written_rows_of(table)` 给「没有另建一行」这类断言（`test_a_thread_owned_by_somebody_else_is_flagged_not_ignored` 用）.
8. **`resume()` 不记账**（照旧）：它那条路本来就不写记录，于是续跑段落的帧 `run_id` 是 None —— 与「没配记录层」同形. 见开放项.
9. 快照与 fixture 各重生成一次（`checkpoint_v6.json` 新增、`checkpoint_frames_tool_path.json` 重生成）：metadata 多了一个 `view` 键. v1~v5 的 fixture 一份没删，全部仍读得回来（新加了一条断言钉住「v5 的 `run_id` → v6 的 `loop_id`，且数据没丢」）.

### 二、碰过的文件

| 文件 | 改动 |
|---|---|
| `CharAgent/checkpoint/utils/types.py` | `Checkpoint.run_id` → `loop_id`；新增 `Checkpoint.run_id`（可空，记录层外键）；`CheckpointMetadata.view`；`SCHEMA_VERSION` 5 → 6 + 版本历史 v6 一行 |
| `CharAgent/checkpoint/utils/migrations.py` | `_v5_to_v6`（键在就改的幂等改名 + 补 `run_id=None`），注册表补一条 |
| `CharAgent/checkpoint/serialization.py` | 整条记录过迁移（原来只 migration body —— Redis 的记录级字段就在整条字典里，不改这里老帧读不回来）；`loop_id` / `run_id` 两个键；metadata 载荷加 `view` + `_read_view` |
| `CharAgent/checkpoint/postgres.py` | 写/读 `loop_id` 与新的 `run_id` 列 |
| `CharAgent/db/schema.py` | 帧表列改名 + 新 `run_id` 外键；运行表加 `last_checkpoint_id`（带 `use_alter`） |
| `CharAgent/db/entities.py` | `Run` / `CheckpointRow` 的属性说明跟上 |
| `CharAgent/db/repositories/runs.py` | `add_terminal` → `finish`；`add` / `_params` 带上 `last_checkpoint_id` |
| `CharAgent/db/repositories/threads.py` | 新增 `set_title`（补空标题） |
| `CharAgent/db/recorder.py` | 协议加 `begin`；`record` / `record_unfinished` 加 `run_id`；`_write` 改成推进那一行 + 写 `last_checkpoint_id`；`normalize_title` 从 `title_for` 里提出来；`_ensure_thread` 收标题字符串并补空标题 |
| `CharAgent/agent/loop.py` | `run` / `resume` 的参数 `run_id=` → `loop_id=`，新增 `run_id=`（记录层编号）；`_compile_view` 存 `state.view`；`_turn_metadata` 带上 `view` |
| `CharAgent/agent/utils/types.py` | `LoopState.loop_id` / `run_id` / `view` 三个字段与说明 |
| `CharAgent/agent/compaction.py` | 新增 `view_payload(compiled, ledger)` |
| `CharAgent/client/session.py` | `_begin_run`（开账）+ `ask` / `resume` 的编号流转 + `_record` / `_record_unfinished` 收 `run_id` |
| `CharAgent/alembic/versions/0004_frame_run_linkage.py` | 新增（改名 + 两列 + 两条外键，含 downgrade） |
| 测试 | 新增 `test_frame_run_linkage.py`（3 例）；`test_loop_compaction.py`（+2 视图入帧）；`test_db_recorder.py`（两拍助手 + 假库升级带来的三处期望修正）；`test_db_store.py`（`finish` 三例）；`test_db_alembic.py`（head 与审计表行数）；serialization / snapshot / resume / session 各文件跟上；`doubles.py`（UPDATE 支持 + `rows_of` 语义 + `written_rows_of`） |

### 三、真机验收（CharApp 服务 + 本机 Postgres + 真 DeepSeek，会话 `fix22`）

六句话（一问一行账），库里三列逐一对照：

| 帧 | `loop_id` | `checkpoints.run_id` | 运行行 | `runs.last_checkpoint_id` |
|---|---|---|---|---|
| 1 | `fe03d0…` | `3488457c…` | `3488457c…` ✓ | `a5b73360…`（= 该帧）✓ |
| 2 | `7cebf8…` | `7bc96e8f…` | `7bc96e8f…` ✓ | `7bf4e7ee…`（= 该帧）✓ |
| … | 每帧各不相同 | 各自指向自己那一行 | 六行 | 各自指向自己那一帧 |

- **帧 → 运行行**：每一帧的 `run_id` 与对应运行行的主键**逐一相等**（此前是两套互不相干的 uuid）
- **运行行 → 帧**：`last_checkpoint_id` 指着本段落下的那一帧，`parent_id` 把六帧串成一条链
- **单请求不再随账本增长**：那六次运行的 `input_tokens` = 5672 / 5824 / 5877 / 5922 / 5909 / 5914

**补一次（2026-09-23 晚，改完列注释重放迁移之后）**：迁移重放会 drop 掉那两列，于是上面那两段会话的 `run_id` / `last_checkpoint_id` 被清空（验收结论是当时查到的值，记录不改）—— 另跑一段三句话的会话 `fix22c` 留在库里，供随时复查：

| 帧 | `loop_id` | 帧的 `run_id` | 运行行的 `run_id` | 一致 | 父帧 | 行 `last_checkpoint_id` |
|---|---|---|---|---|---|---|
| 1 | `37d84a` | `7b23d879` | `7b23d879` | ✓ | 根 | `bb565e9e`（本段的末帧）✓ |
| 1 | `8f32ab` | `403d064f` | `403d064f` | ✓ | `bb565e9e` | `2a6b1893`（**该段末帧**，不是这一帧）—— 一问两帧时，行指的是最后一帧 |
| 2 | `8f32ab` | `403d064f` | `403d064f` | ✓ | `8bd37892` | `2a6b1893` ✓ |
| 1 | `41ab7d` | `d3ef011b` | `d3ef011b` | ✓ | `2a6b1893` | `21179625` ✓ |

顺带：第三句问「我的幸运数字是几」答出 `66`（第一句给的）—— 上下文照旧接得上（这一段没触发压缩，走的就是全量账本）.

另一段会话（`fix22b`，八句话，阈值 9000）用来验第 4 件（视图入帧）与 issue 23 的修复，逐帧读 `metadata.view`：

| 帧 | 账本条数 | 记下的视图消息 | 带摘要 | 丢 / 截 |
|---|---|---|---|---|
| 1–6（没超阈值） | 3 → 34 | None（**视图就是账本**，不抄第二份） | — | 0/0 |
| 7（切一刀） | 36 | 19 条 | ✓ | 18/1 |
| 8（刚压完，低于阈值） | 38 | 21 条 | ✓ | 0/0 |

第 8 帧同时是 issue 23 的正面证据：账本 38 条、发出去的只有 21 条且带摘要（修之前这一轮会把全量发出去）.

### 四、开放项

| # | 项 | 说明 |
|---|---|---|
| 1 | `resume()` 不记账 → 续跑段落的帧 `run_id` 是 None | 今天与「没配记录层」同形. 要不要给 resume 也开一行账（那样「一次续跑」在账目里也可见），属于新行为，留给后面拍板 |
| 2 | 进程被杀会留下 `running` 的悬挂行 | 今天什么都不留，所以是**多**出来的（换来「跑到一半在库里看得见」）. 兜底（同一会话开新运行时收掉陈旧行）默认不做 |
| 3 | 别的部署要跑 `0004` | 本机已跑（并验过 downgrade → upgrade 一遍）. 老帧的 `loop_id` 由 SQL 改名保留、`run_id` 补 NULL；Redis 那边由 `_v5_to_v6` 在读取时改名 |
| 4 | `metadata.view` 的体积（真机量过一次） | 会话 `fix22b` 的 18 帧：**17 帧** `view.messages` 是 `null`（视图就是账本，不存），metadata 合计 8.1 KB（平均 ~477 B/帧，只带计数）；**2 帧**（压过的）存了视图，metadata 合计 17.4 KB（~8.7 KB/帧，比同帧的 state 还大一点 —— 里面是摘要正文 + 19 条消息）. 也就是说代价集中在「视图真的不一样的那几轮」，而那正是审计要看的那几轮；要更省可以只存摘要与计数（读时重算视图），但那就丢了「逐字一致」这条保证 |

### 五、验证到哪一步

`pytest`：CharAgent **992 passed / 74 deselected** · 标记集 `-m "pg or pg_db or redis"` **63 passed**（真 Postgres / 真 Redis，含迁移一致性 `compare_metadata` 零差异）· CharApp **198 passed**；`ruff check` / `ruff format --check` 干净（全仓）. 真机：上面的两张表（三列逐一相等 + 视图入帧），另外 `alembic check` 零差异.

### 六、代码审查改了什么（Standards / Spec 两轴各起一个 sub-agent，2026-09-23）

**改掉的（9 处）**：

1. **`create_tables([checkpoints])` → `[threads, runs, checkpoints]`**（Spec 点名的缺口）：帧的新外键指着 `charagent_runs`，后者又指着 `charagent_threads` —— 只建帧表，Postgres 会以「被引用的表不存在」拒绝，而现象是**第一次存帧**才炸。pg 用例一直没照出来，是因为夹具先建了全表把它掩盖了；现在补了 `test_the_saver_creates_the_tables_it_depends_on`（先把三张表删干净，再让 saver 自己建）。
2. **新增真库用例 `test_frames_and_runs_point_at_each_other_and_unlink_on_delete`**（pg_db 标记）：帧 ↔ 运行行两条方向都对得上，且两条外键都是 `ON DELETE SET NULL`（删一头时另一头置空、不跟着消失）—— 票里点名要的库层行为，此前只有假库用例。
3. **新增「两个编号各自往返」用例**：`run_id` 非空时的往返（fixture 与快照都是 null，此前没人守有值那一半）。
4. **新增「补做那一轮的帧里 `view` 是 None」用例**：挂起补做没有模型调用，不该把上一轮那份抄进来（配了压缩策略才说明问题）。
5. **`metadata.view` 的计数抽成 `compaction.compiled_counts()`**（Standards：与 `context_compacted_data` 各组装一遍同样六个键）：现在「这次压缩做了什么」只有一个定义，两个下游各拼自己多出来的那个键（事件多 `turn`、帧多 `messages`）。
6. **术语收口**：「一个 run 的几帧共享 `loop_id`」这类说法改成「一次循环执行的几帧」—— 那句话里的 run 与新的 `run_id`（记录层那一行）撞名，正是本片要消掉的乱（`db/entities.py` / `checkpoint/utils/types.py` / `db/schema.py` / 迁移注释四处）。
7. **测试里的编号值跟着改名**：`run-1` / `run-x` / `run-branch` / `run-compaction` → `loop-*`（参数早已叫 `loop_id`，值还留着旧名字属于改了一半）。
8. **`begin` 失败不再报两条日志**：失败发生在开账那一拍，`begin` 已经记过（带 DB 错误），收尾那拍只留「欠一条提示行」的标记。那条用例的断言跟着改成「只有一条告警」。
9. **几处陈旧说法**：记录员模块 docstring 的「每次运行收尾写一次」→ 两拍；`ChatSession` 的 `recorder` 参数说明「那两个方法」→ 三个；`check_identifier` 的「校验 thread_id / run_id」→ `loop_id`；`_save_checkpoint` 的「存档永远是全量」补上「说的是账本那一块，这一轮发出去的那份另记在 `metadata.view`」。

**看了但不改的（记理由）**：

- **`metadata.view` 是裸 `dict` 而不是 dataclass**（Speculative/Primitive Obsession）：同族先例是 `CheckpointState.prompt_ref`（也是自由字典 + docstring 写死键的含义），而这里刻意不逐字段校验 —— 加一个计数就得改编解码两处的代价更大；键的含义写在 `view_payload` 与 `CheckpointMetadata.view` 两处 docstring 里。
- **`view_payload` 存的是 `compiled.messages` 的引用**（与 `MockLLM.calls` 共享同一批 dict）：与 `TurnRecord(messages=list(state.history))` 同一种做法（浅拷贝 + 消息 dict 追加后不再变更），全仓一致的约定，不是本片新引入的。
- **中文顿号 / 句号**（Standards 标出的 14 处）：本仓既有 421 处同款写法，按「匹配现有风格」不在本 diff 单点改（与 issue 18 那次审查同一条处置）。
- **`_ensure_thread` 的「补空标题」+ `set_title` 曾被删掉后恢复**：审查认为它「生产不可达」，但 `begin` 的 `title` 在 protocol 里默认是空串（`record_turn` 这样的调用方就不带标题，框架 CLI / 未来的 resume 开账同理）—— 不补的话那段会话在前端左栏永远没有名字。恢复时把理由写准（不是「那时还没有标题可写」，而是「那一拍未必带着标题」）。

### 七、验证到哪一步（补完审查改动之后）

`pytest`：CharAgent **994 passed / 74 deselected** · 标记集 `-m "pg or pg_db or redis"` **65 passed**（新增两条真库用例）· CharApp **198 passed**；`ruff check` / `ruff format --check` 干净（全仓）；`alembic check` 零差异。

> 记录人：Claude Code (charlotte) · 2026-09-23
