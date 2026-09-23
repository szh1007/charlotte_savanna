# 32 · 框架侧：幂等持久化（`idempotency_keys` 表 + PG store）

**Status:** todo

**Type:** task

**Blocked by:** 无（可与主线并行）

**上游:** `CharAgent/docs/DESIGN.md` #17（幂等 + Saga 补偿）；`CharApp/docs/PLAN.md` §5 的 L3b 段第 1 条；**ADR-0017**（本片是那条改判的执行者）；调用方是 issue 34

## 现状（2026-09-24 核实）

**协议与内存实现都在，持久化与调用方都不在**：

| 零件 | 位置 | 状态 |
|------|------|------|
| `IdempotencyKey` | `retry/idempotency.py:58-96` | 构造即校验：长度 1-255（`MAX_KEY_LENGTH:52`）、字符白名单 `KEY_PATTERN:55`（`^[A-Za-z0-9][A-Za-z0-9._:-]*$`） |
| `IdempotencyStore` 协议 | `retry/idempotency.py:107-124` | 三个方法：`claim(key) -> ClaimResult` / `complete(key, result=None)` / `release(key)` |
| `ClaimStatus` / `ClaimResult` | `retry/utils/types.py:22-27` / `:30-45` | 三态 `CLAIMED` / `IN_PROGRESS` / `COMPLETED`；`ClaimResult.claimed` 便利属性 `:42-45` |
| `InMemoryIdempotencyStore` | `retry/idempotency.py:135-169` | 纯 `dict[str, _Record]`，**无 TTL / 无持久化 / 跨实例失效** |
| **PG / Redis 实现** | —— | **零**（`grep IdempotencyStore` 只命中模块自身、测试、门面再导出） |
| **生产调用方** | —— | **零**。本片**也不产生**调用方（那是 issue 34） |
| 现存的唯一幂等兜底 | `runs.request_id` 唯一约束（`db/schema.py:294`）+ `RunsRepository.get_by_request_id`（`db/repositories/runs.py:194-202`） | 给「`POST /runs` 重复提交」用的，**与本片不是一件事** |

`db/README.md:138-153` 早就规划好了这一张表：**P1 只加 `idempotency_keys` 一张**，理由与追加流程都写在那里。

## 本片要补的三件

### 一、表 `charagent_idempotency_keys`

按 `db/schema.py` 的既有风格（`charagent_` 前缀、**每列必须写 `comment=`** —— 由 `tests/test_db_schema.py::test_every_column_has_a_comment` 强制）：

| 列 | 类型 | 说明 |
|----|------|------|
| `key` | `String(255)` **PK** | 幂等键本身（长度上限与 `MAX_KEY_LENGTH` 同源） |
| `status` | `String` NOT NULL | `ClaimStatus` 三值 |
| `result` | `JSONB` NULL | 完成时的结果（`complete(key, result)` 存的东西） |
| `expires_at` | `DateTime(timezone=True)` NULL | 过期时刻；NULL = 不过期 |
| `created_at` / `updated_at` | `DateTime(timezone=True)` NOT NULL | 认领 / 状态最后变化的时刻 |

**加表会打破一条既有用例**：`tests/test_db_schema.py:41-56` 断言表名集合与 **`len(ALL_TABLES) == 5`** —— 必须同步改成 6，并把它加进 `ALL_TABLES`（`db/schema.py:549-555`，按依赖序）与 `TABLE_NAMES`（`:558`）。

### 二、仓储 + PG store

- 仓储放 `db/repositories/idempotency.py`，继承 `PgRepository`（`db/repositories/base.py:37-57`，只需 `__init__` + `_session` 两件）
- `PgIdempotencyStore` 实现 `IdempotencyStore` 协议。**它住在哪要定一下**：协议在 `retry/`，存储实现在 `db/` —— 倾向**放在 `db/repositories/idempotency.py` 里一并导出**，让 `retry/` 保持"零数据库依赖"（它现在的 import 只有标准库 + `retry.utils`）
- **`claim` 必须靠数据库的原子性，不能"先查后插"** —— `db/schema.py:292-293` 那条注释就是为这件事写的（原文：「应用层先查后插有并发窗口, 唯一约束才是真正不会漏的那道闸」）。用 `INSERT ... ON CONFLICT DO NOTHING` + 返回行数判断"这次是不是我认领的"
- **`release` 只放 `IN_PROGRESS`**，已完成的不动（与内存实现同语义，`retry/idempotency.py:162-169`）

### 三、TTL：加列，不做后台清理

内存实现无 TTL 那条被列在 P0 边界里。PG 版**加上 `expires_at` 列与查询侧判定**（`WHERE key = ? AND (expires_at IS NULL OR expires_at > now())`），但**不做后台清理线程**：

- 清理是运维动作，与"能挡住重放"无关 —— 而本片的目标只是后者
- 一个常驻清理任务要多一处收尾、多一个测试面，收益是"表小一点"
- 这条边界**写在这里而不是留白**：将来表真的涨起来了，加一条 `DELETE FROM ... WHERE expires_at < now()` 的定时任务即可，表结构不用改

**过期记录的语义要定死**：过期之后同一个键**可以重新认领**（它已经不是同一次操作了）。这与"永不过期"是两种口径，实施时任选其一都可以，但要**写进列注释**。

## 键的形态（由调用方决定，本片只约束）

`KEY_PATTERN` 只允许 `[A-Za-z0-9._:-]`，所以 `(run_id, message_id, tool_call_id)` 三列拼起来要**落在白名单里** —— uuid 的 hex 与 `:` / `-` / `.` 都合法。**三列不是两列**：上游每轮从 `call_0` 重新编号，少了 `message_id` 在多轮之间会撞（见 `db/schema.py:364-450` 那段主键注释）。

本片**不定义**具体拼法（那是 issue 34 的调用方的事），但**要在用例里钉住"超长/非法字符会被 `IdempotencyKey` 挡住"**，否则调用方拼错了会直到写库才炸。

## 交付物

| # | 内容 |
|---|------|
| 1 | `db/schema.py` 加 `charagent_idempotency_keys` 表 + `ALL_TABLES` / `TABLE_NAMES` |
| 2 | 迁移 `0003_<slug>.py`（`down_revision = "0002_thread_management"`，现 head） |
| 3 | `tests/test_db_schema.py` 的表数断言 5 → 6 |
| 4 | `db/repositories/idempotency.py`：仓储 + `PgIdempotencyStore` |
| 5 | 用例：三态转换、并发认领只有一个成功、`release` 只放 `IN_PROGRESS`、过期键可重认领、非法键被挡 |
| 6 | `db/README.md` 的追加表一节把 `idempotency_keys` 从"P1 待办"挪成"已建" |

## 验收

- [ ] `alembic upgrade head` + `tests/test_db_alembic.py:241` 的"代码侧与库侧零差异"门通过
- [ ] `pytest -m pg_db`（真库）通过；**并发用例是真的并发**（两个 `claim` 抢同一个键，恰好一个拿到 `CLAIMED`）
- [ ] 每列都有 `comment=`（既有用例强制）
- [ ] 现有 1052 条用例不受影响；`retry/` 包**没有**新增数据库依赖（它仍然零 DB import）
- [ ] `idempotency_keys` 表建好了但**还没有调用方** —— 这是本片的预期终态，不是漏了（调用方归 issue 34）

## 备注

- **不做 Saga 补偿**：DESIGN #17 的标题里有它，但 HITL 的场景不需要（挂起-恢复的重放只有一个动作，没有"正向多步"）。**不做要写明理由**，别让它看起来像漏了一半。
- **与 `runs.request_id` 的关系**：两者都是幂等，但挡的东西不同 —— 那个挡「同一次 HTTP 提交重复建 run」，本表挡「同一条工具调用被执行两次」。**不要合并**，也不要让 `POST /runs` 改用本表。
- 本片**只做框架侧**：`retry/` 的模块 docstring 已经在 ADR-0017 那一轮改过（记了"第一个真实调用方是 HITL 挂起-恢复"），本片落地后可以再把"存储已持久化"那句的时态改一下。
