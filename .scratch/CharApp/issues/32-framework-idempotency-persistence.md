# 32 · 框架侧：幂等持久化（`idempotency_keys` 表 + PG store）

**Status:** done

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

- [x] `alembic upgrade head` + `tests/test_db_alembic.py:241` 的"代码侧与库侧零差异"门通过
- [x] `pytest -m pg_db`（真库）通过；**并发用例是真的并发**（两个 `claim` 抢同一个键，恰好一个拿到 `CLAIMED`）
- [x] 每列都有 `comment=`（既有用例强制）
- [x] 现有 1052 条用例不受影响；`retry/` 包**没有**新增数据库依赖（它仍然零 DB import）
- [x] `idempotency_keys` 表建好了但**还没有调用方** —— 这是本片的预期终态，不是漏了（调用方归 issue 34）

## 实施记录（2026-09-25）

**交付物六条全部落地**，两处与票据原文不同（都是同一天发生的事实变化，不是取舍）：

| 票据原文 | 实际做法 | 为什么 |
|---------|---------|--------|
| 迁移 `0003_<slug>.py`，`down_revision = "0002_thread_management"` | **`0004_idempotency_keys.py`**，`down_revision = "0003_run_cost_columns"` | 票据写于 09-24；**同一天** ticket 28 的 `0003_run_cost_columns` 先落地成了 head（成本口径改判）。连带改的还有 `test_db_alembic.py` 里三处版本号断言与「走三步」那条（现为四步） |
| 「仓储 + `PgIdempotencyStore`」两个名字 | **一个类**：`PgIdempotencyStore(PgRepository)`，就在 `db/repositories/idempotency.py` | 这张表的「业务概念」只有一件事（认领），而它已经由 `retry/` 的协议说全了 —— 再拆一层业务形状会得到一个只做转发的中间层（协议 → 仓储 → store）。它仍归 `db/repositories/`（**要用库**），协议与内存实现仍归 `retry/`（**零数据库依赖**），依赖方向单向不变 |

**一处对票据措辞的澄清（行为与内存实现完全一致，改的是说法）**：票据说 `status` 列是「`ClaimStatus` 三值」，实际落库的只有两值 —— `claimed` 是**本次认领成功的答复**，不是存下来的状态（认领成功写下来的是 `in_progress`）。`InMemoryIdempotencyStore` 的 `_Record` 也是这么存的。这一条写进了列注释。

### 关键实现选择（都在代码注释里写明了理由）

- **认领的原子性靠数据库**：一条 `INSERT ... ON CONFLICT (key) DO UPDATE ... WHERE expires_at <= <now> RETURNING key`。插进去（或改写了过期行）的才是抢到的那一个；没抢到的再读那一行，`completed` 回结果、其余一律回「有人在办」（**安全的那一侧**：不会让调用方再执行一次）。
- **过期挤在同一条语句里判**：`WHERE expires_at <= now` 恰好是票据那句 `expires_at IS NULL OR expires_at > now()` 的补集 —— NULL（永不过期）与未来时刻都不满足，行一动不动。过期后可重新认领，且**旧结果不会被当成新一次的**（认领把整行改写成新操作）。
- **`release` = 删掉在途那一行**（与内存实现的 `del` 同语义），已完成的行不动；`complete` 无认领记录时补插一行（同内存实现的兜底）。
- **默认不过期**（`ttl_seconds=None`）：内存实现也没有 TTL，默认值短命会让「换成持久化实现」变成保护范围被悄悄改小。
- **不做后台清理**（票据已定）：`expires_at` 有列、有判定，没有定时任务 —— 将来加一条 `DELETE` 即可，表结构不用改。
- **两处没进代码的边界**（写进 docstring）：`owner 校验`（同一把键过期后被重新认领时，前一手的 `complete` 会写进后来那一手的行 —— 属 P1 的 Redis 版）与 `Saga 补偿`（HITL 的挂起-恢复没有「正向多步」，用不上）。
- **多做了一件票据没要求的**：`ttl_seconds <= 0` 在构造期报 `DataConfigError`。理由：0 与负数会让每把键认领完立刻可再认领，即**这个存储装上了却什么都挡不住，且不报任何错** —— 正是本仓「配置错要在造对象那一刻报出来」那条（对齐 `RetryPolicy.__post_init__`）。

### 真机一次（2026-09-25，本机 Postgres，`public` schema，走 `.env` 的 `PGSQL_*`）

本片**没有调用方**，所以真机能验的是「迁移 + 这个存储直连真库」，验不到「服务里接上了」那一段（那是 issue 34）。步骤与结果：

```
① 首次认领          -> claimed      (claimed=True)
② 同一条请求再来一次 -> in_progress  (claimed=False)      ← 重放被挡住的那一刻
③ 动作做完 + 认领   -> completed    结果={'payment_id': 'p-1', 'amount': '19.90'}
④ 认领后 release     -> 再认领 = claimed                   ← 失败后放行合法重试
⑤ ttl=1 秒, 真等 1.3 秒 -> 再认领 = claimed                ← 过期后可重新认领
⑥ 直接查库: 3 行
   demo32:order-1:msg-a:call_0 | completed   | {'amount': '19.90', 'payment_id': 'p-1'} | 2026-09-25 15:30:40+08
   demo32:order-2:msg-a:call_0 | in_progress | None                                     | 2026-09-25 15:30:40+08
   demo32:order-3:msg-a:call_0 | in_progress | None                                     | 2026-09-25 14:30:42+08   ← 已过期
```

- **迁移**：`alembic upgrade head` 把开发库从 `0003` 推到 `0004`；库里那一行（版本表兼审计表）现在记着 `version_num = 0004_idempotency_keys`、标题「幂等登记表: 一条键一行, 挡住「同一个动作被执行两遍」」。
- **库里的表**核对过：6 列类型 / 可空 / 列注释 / 表注释与 `db/schema.py` **逐字一致**，主键名 `pk_charagent_idempotency_keys`，0 行（演示行已清掉，`demo32:%` 那 3 行）。
- **回退也真跑过**：改完注释后用 `downgrade 0003_run_cost_columns` + `upgrade head` 重来一次（表当时是空的）—— 这正是本仓对「自己这一片、还没提交的迁移」的既定做法（0003 的 docstring 里有先例）。

### 用例（12 条，`-m pg_db`）

三态转换 / 并发（两个认领各起线程与事件循环，5 轮）/ `release` 只放 `IN_PROGRESS` / 补登 / 过期可重认领 / 过期的 COMPLETED 重来不带旧结果 / 不配 ttl 则永不过期 / ttl 配错报错。另有 1 条在**默认就跑**的 `test_retry_idempotency.py` 里：三列拼法的键合法则通过、超长与夹了空白则在**进存储之前**被挡住（票据 §「键的形态」要的那条）。

**这两条并发用例是真验过的**（不是跑绿就算）—— 把实现临时改坏，看它红不红：

| 临时改法 | 结果 |
|---------|------|
| 去掉 `ON CONFLICT ... WHERE expires_at <= ...` 那条条件 | **8 条红**（全部键都可重认领） |
| 换成「先查后插」（先 `SELECT` 再裸 `INSERT`） | **并发用例红**，报 `UniqueViolation`（两个都以为自己是第一个）—— 第一版并发用例（同一个事件循环里 `gather`）**抓不到**它，因为同一个循环里 `session.execute` 是同步的、两条语句根本插不到一起；改成各起线程 + 各起事件循环之后才真正并行 |

### 两轴复核（`/code-review`）后的修补

- **全角句号**：新写的 4 个文件里有 41 处 `。` 混进 Python 注释/文档字符串，与 CLAUDE.md §4.9「标点一律英文」和邻座文件的实际风格都不符 —— 全部改回 `.`（Markdown 的 `db/README.md` 不在此列）。
- **列注释与代码不符**（Spec 轴发现）：`expires_at` 原写「它只在认领时写, 完成与释放都不改它」，但 `complete` 的**补登**那条路会写它 —— 注释改准（两个文件同步改，改完在真库上重跑了一次迁移）。
- **顺带订正的旧计数**：`schema.py` 的 metadata 注释、`db/README.md` 的版本表段（都还写着「五张」），以及 `db/database.py` / `alembic/env.py` 的同一处计数。
- 其余三小件：`_claim_in_its_own_loop` 补返回类型、`db/__init__.py` 少一个空行、`test_retry_idempotency.py` 那条超长键的断言改点名「实际长度 266」（原文案指的是字符集，不是真因）。

## 改了哪些文件

| 文件 | 改了什么 |
|------|---------|
| `CharAgent/db/schema.py` | 新增 `idempotency_keys` 表（6 列 + 表注释）；`ALL_TABLES` / `TABLE_NAMES` 五 → 六；模块 docstring 与 metadata 注释里的计数 |
| `CharAgent/alembic/versions/0004_idempotency_keys.py` | **新文件**：`op.create_table` + `drop_table`（逐列注释与 `schema.py` 逐字一致） |
| `CharAgent/db/repositories/idempotency.py` | **新文件**：`PgIdempotencyStore`（claim / complete / release + `_now` / `_expires_at`） |
| `CharAgent/db/repositories/__init__.py` · `db/__init__.py` · `CharAgent/__init__.py` | 导出 `PgIdempotencyStore`（三层门面同一份名单，`test_root_facade.py` 强制） |
| `CharAgent/db/README.md` | 表清单/取数口计数；「既有迁移」两条 → 四条；P1 行从「待办」改成「已建」 |
| `CharAgent/retry/idempotency.py` | 只改 docstring：P0 边界那句时态（持久化那一半已落地）、协议与内存实现的去向、仍没做的两件（owner 校验 / Saga） |
| `CharAgent/tests/test_db_idempotency_store.py` | **新文件**：12 条真库用例 |
| `CharAgent/tests/test_db_schema.py` | 表清单加第六张；表数 5 → 6；主键断言补幂等表 |
| `CharAgent/tests/test_db_alembic.py` | 版本号断言 `0003` → `0004`；「走三步」→ 四步；标题/来源版本 |
| `CharAgent/tests/test_retry_idempotency.py` | 新增 1 条：三列拼出来的键合法则通过、拼歪了在进存储之前被挡住 |

**没动的**：`CharAgent/db/entities.py`（这张表**不加实体** —— 它没有「业务视角」的那一半，理由写进 `schema.py` 与 `db/repositories/__init__.py`）、`CharAgent/db/recorder.py`、`checkpoint/`（与本片无关）、`runs.request_id` 那条既有幂等兜底（**不合并**：它挡「同一次 HTTP 提交重复建 run」，本表挡「同一条工具调用被执行两次」，见票据备注）。

## 备注

- **不做 Saga 补偿**：DESIGN #17 的标题里有它，但 HITL 的场景不需要（挂起-恢复的重放只有一个动作，没有"正向多步"）。**不做要写明理由**，别让它看起来像漏了一半。
- **与 `runs.request_id` 的关系**：两者都是幂等，但挡的东西不同 —— 那个挡「同一次 HTTP 提交重复建 run」，本表挡「同一条工具调用被执行两次」。**不要合并**，也不要让 `POST /runs` 改用本表。
- 本片**只做框架侧**：`retry/` 的模块 docstring 已经在 ADR-0017 那一轮改过（记了"第一个真实调用方是 HITL 挂起-恢复"），本片落地后可以再把"存储已持久化"那句的时态改一下。
