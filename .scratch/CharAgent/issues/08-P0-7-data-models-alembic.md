# 08-P0-7 — 五实体数据模型 + alembic 初始化

**What to build:** 五核心实体（Thread / Run / Message / ToolCall / Checkpoint）数据模型定义，P0 一次定死（含 schema 版本号向前兼容），Postgres 表 + alembic 首次迁移初始化。字段与协议定型一致：Run 状态机（created/running/waiting_tool/waiting_user/retrying/failed/finished/cancelled）、Message.reasoning 独立成列（供重连重建，前端折叠展示；注意它**同时**回填 wire 历史，见 issue 04 §1 修正后的 #11 契约）、ToolCall.status 含 needs_approval（HITL）、Checkpoint.parent_id 分支来源。demo 表（tickets/escalations/approvals/audit_logs）与幂等表预留为 P1 迁移。

**Blocked by:** 04

**Status:** ready-for-agent

- [x] 五实体模型定义完成，字段与协议定型一致（#5/#12 实体部分）
- [x] Postgres DDL 生成（alembic），首次迁移在空库可执行
- [x] 实体字段与状态机枚举一致（Run/ToolCall status 全部取值）
- [x] 迁移体系预留 P1 demo 表与 P2 event 表追加路径（#12）
- [x] 会话消息与 agent transcript 分层落库：`GET /threads/{id}/messages` 只应返回一问一答（user 提问 + `LoopResult.content`）；agent 内部 transcript（含 role=system 的续写指令、role=tool 的工具回填）归 Checkpoint / 观测，不可直接喂前端。注意 CONTINUE 场景 `content` ≠ `messages[-1].content`（前者是截断续写的跨段拼合结果）

## Comments

> **阅读指引**：本 issue 是**边做边记**的，所以下面各节的「当时状态」与「当前状态」
> 不同 —— §1~§5 是首版交付的记录（当时包名还叫 `models/`），§6 之后的 A~F 是随后几轮
> 核对/审计的修正。**要了解当前状态，看 §6.F6 的全量复核**；§1~§5 里的路径已订正为
> 现名，数字指标保留原值（那是当时的证据，不该改）。
>
> 最终提交：`ff90df4 feat(CharAgent): issue-08 add five-entity data model and alembic init`
> （46 文件，+6537 / -331）。

**2026-09-14 实施完成**。落点 `CharAgent/db/`（新包，当时叫 `models/`，见 §6.E 的改名）+ `CharAgent/alembic/`（新目录），并**顺带把 P0-6 的 checkpoint Postgres 实现从裸 psycopg 统一到 SQLAlchemy**（用户在本 issue 期间追加的要求：两套数据访问并存有管理成本、容易误导）。

### 1. 交付清单

| 文件 | 职责 |
|------|------|
| `db/schema.py` | **表定义唯一来源**（五张 `Table`）—— 迁移、快照存储、仓储都从这里取 |
| `db/entities.py` | 五实体（`Thread` / `Run` / `Message` / `ToolCall` / `CheckpointRow`）+ 四个状态枚举 |
| `db/state.py` | 合法迁移表 + 终态集合 + `LoopOutcome → RunStatus` 映射（纯规则，不碰数据库） |
| `db/conversation.py` | 会话消息分层 + 最终答复的取值口径（issue 第 5 条验收的核心） |
| `db/database.py` | `PgDatabase`：同步引擎 + `asyncio.to_thread` 的门面（一次 `connect()` = 一次事务） |
| `db/config.py` / `errors.py` | 连接配置 / 三类错误 |
| `db/repositories/` | 四个取数口 + `utils/mapping.py`（打包拆包） |
| `alembic/` | `alembic.ini` + `env.py` + `versions/0001_charagent_core.py`（五表首次迁移） |
| `db/README.md` | 维护手册（含 P1/P2 追加路径 —— 验收第 4 条） |
| **删除** `checkpoint/utils/ddl.py` | 表定义并入 `db/schema.py`，一份定义供快照存储建表与 alembic 迁移共用 |

提交：`ff90df4`（46 文件，+6537 / -331）。其中 29 个新增（`db/` 16 个模块 + `alembic/` 4 个 + 测试 8 个 + README）、17 个修改（`checkpoint/` 5 · `docs/` 6 · checkpoint 测试 3 · `pytest.ini` · `conftest.py` · 本 issue）、1 个删除。

### 2. 三个决策与理由

**A. 用户选定：SQLAlchemy 2.0 ORM + autogenerate、全 `charagent_` 前缀、交付含仓储。**
ORM 形态下的两处现实约束（都是实测逼出来的，不是偏好）：

- **仓储与快照存储走「同步引擎 + `asyncio.to_thread`」**：psycopg 的异步连接在 Windows 默认的 `ProactorEventLoop` 上直接报错（`InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`），SQLAlchemy 的 `create_async_engine` 底层就是它，同样跑不起来（2026-09-14 实测）。换事件循环等于给使用方加前置条件 —— 改走同步驱动 + 线程池，对外仍是 async 接口。Django 的 `sync_to_async` 走的是同一条路。
- **`checkpoint/` 与 `db/` 共用连接层**：`PgDatabase` 顺带取代了 P0-6 那条「一条连接 + 一把 asyncio 锁排队」的权宜设计（有了连接池就不需要它）。

**B. 表定义从「手写 SQL + 另有 alembic」收敛为一处。**
P0-6 的 `checkpoint/utils/ddl.py` 已删除，内容并入 `db/schema.py`。这不是「多了一个对比测试」，而是**少了一份定义**。

**C. 快照表有两份「视图」但只有一份定义。**
`CheckpointRow`（库里的行）与 `checkpoint.utils.types.Checkpoint`（运行时对象）是两回事，翻译留在 `checkpoint/postgres.py`（那里本来就有 codec）。依赖方向单向：`checkpoint → db`，`db` 不知道 checkpoint 存在 —— 否则两边互相 import 就是死循环。

### 3. 实施中发现并修正的两处真实设计缺陷

**① `tool_calls` 的复合主键少一维（P0 设计的一个洞）。**
issue 原文与 `02-data-model.md` 都写「`tool_call_id` 单列」，P0-6 的 `pending.py` 又记载了「上游每轮都从 `call_0` 重新编号」—— 两者放一起就是矛盾：同一个 run 里第二轮的 `call_0` 会撞键。
修法：主键定为 **`(run_id, message_id, tool_call_id)` 三列**。加 `run_id` 只解决「跨 run」，同一个 run 的多轮之间照样撞 —— 真正唯一的身份是「**哪条 assistant 消息**发起的这一次调用」（上游每轮恰好发一条带 tool_calls 的 assistant 消息）。因此 `message_id` 从可空改为必填，外键动作随之从 `SET NULL` 改为 `CASCADE`（主键列不能为空）。用例 `test_same_tool_call_id_across_two_turns_does_not_collide` 钉住这一点。

**② `PgDatabase.connect()` 漏了「事务体里的 SQL 错误」这条路径。**
初版只在提交时包装异常，事务体里 SQL 报错会裸着抛 `sqlalchemy.exc.DataError` 出去 —— 与 docstring 承诺的 `DataStoreError` 不符。用例 `test_failed_transaction_leaves_nothing_behind` 抓到后补上（回滚 + 包装 + `from` 保留原因）。

### 4. 验收证据

> 下面这组是**首版交付时**的数字（§6.E 改名之前，所以按旧名 `-m pg_models` 写）。
> 命令现在跑不通 —— marker 已改为 `pg_db`（数字见 §6.F6）。保留原值是因为那是当时的证据。

- **默认全量**（零外部依赖）：`531 passed / 55 deselected`
- **真库 · checkpoint**：`pytest -m pg` → `19 passed`（既有用例，重构后原样通过）
- **真库 · db 层**（当时叫 models）：`pytest -m pg_models` → `22 passed`（alembic 迁移 6 + 仓储 16）
- **真 Redis**：`pytest -m redis` → `3 passed`
- **真实端点回归**（改动过 checkpoint，按约定必跑）：`pytest -m integration tests/integration/test_loop_real.py` → `3 passed`
- Ruff check + format：零告警
- 隔离方式：真库用例在**独立 schema**（`charagent_test` / `charagent_alembic_test`）里干活，跑完整个删掉 —— 开发库的 `public` 一个字节都不动

其中最有说服力的两条：

- `test_db_alembic.py::test_no_difference_between_code_and_migrated_schema` —— 迁移之后让 **alembic 自己**比对「代码定义 vs 库里结构」，零差异；
- `test_db_conversation.py::test_continuation_answer_uses_loop_result_content_not_last_message` —— 把 CONTINUE 场景的 `messages[-1]["content"]` 与 `LoopResult.content` 写成**不同值**，断言拿到的是拼合后的那个。抄尾段就会在这里红。

### 5. 未采纳（附理由）

- **async ORM / async psycopg** —— 本机实测 Windows Proactor 直接报错；同步引擎 + `to_thread` 是唯一能在本机跑通的路。
- **`relationship()` + 懒加载** —— Python 3.13 + SQLAlchemy 2.0.50 上 `lazy="dynamic"` 已弃用，异步下懒加载是经典坑；显式 join 更可控（需要时再加 `selectinload`）。
- **仓储铺满 CRUD** —— 只写 P1-1 / P1-2 明确要用的方法（与 P0-6 当初不做 `delete_thread` 的理由一致）。
- **给 `CharAgent/__init__.py` 加 models 导出** —— 既有不一致（agent / stream / hooks / retry / checkpoint 都未顶层导出），同批处理，不在本 issue 单独破例。
- **保留 `ddl.py` 的 SQL 再拿测试对比两份定义** —— 表定义已统一到 SQLAlchemy，两份定义 + 一个对比测试是纯负债。

### 6. 交付后修正（2026-09-14，用户核对时发现）

**A. 版本表也带上 `charagent_` 前缀**（用户提出）。alembic 默认把版本记录写进
`alembic_version` —— 一个不带项目标识的通名。本项目各子项目**共用同一个 PG 库**，
而这张表是「谁先跑谁的」：另一个子项目再跑 alembic 会读到**我们**的版本号，于是
「表还没建」却被判定为「已到最新版」，迁移被静默跳过 —— 与五张业务表撞名是同一
类问题。改为 `charagent_alembic_version`（名字定义在 `alembic/env.py` 的
`VERSION_TABLE`，只那一处）。

**B. 开发库按迁移重建了一遍**。核对时发现库里的结构与迁移文件有真实差异 —— 因为
迁移的 `0001` 在**中途**被我改过（补列注释、`tool_calls.message_id` 外键改
CASCADE），而改之前它已经跑过一次。重建后 `--autogenerate` 零差异。

差异清单（重建前）：

| 项 | 重建前 | 应然 |
|----|--------|------|
| `charagent_checkpoints` | 已被用户删除 | 存在 |
| `tool_calls.message_id` 外键 | `SET NULL` | `CASCADE`（它是主键的一部分） |
| `messages.hidden` 等 3 处列注释 | 旧文案 | 与 `schema.py` 逐字一致 |

**教训**：迁移脚本落地后就不能再改（本 issue 的文档与 `db/README.md` 都把这条
写成规矩，我自己在实施中途破了它）。正确做法是那几处调整**新开一条 `0002`**，
而不是回头改 `0001`。开发库当时全空，重建零成本；生产环境遇到同样情况就得写一条
补偿迁移。

**C. 快照表改用 `op.create_table` + `op.create_index`**（用户提出）。原先它是
`op.execute` 的一串 `CREATE TABLE IF NOT EXISTS` + `COMMENT ON` + 幂等建索引 ——
那套幂等写法只为兼容 P0-6 遗留的旧表。旧表已随重建消失，理由也不再成立，于是改成
和另外四张表**完全一致**的写法：

- 列注释从 SQL 字符串挪进 `comment=` 参数（不再另跑一串 `COMMENT ON`）
- 索引从 `CREATE INDEX IF NOT EXISTS` 改成 `op.create_index`
- `downgrade` 里的 `DROP INDEX IF EXISTS` 变通随之删掉（那个坑其实是当时残留的
  probe 迁移造成的，不是 `op.drop_index` 本身有问题 —— 现已实测走通）

**D. 五张表统一补上表级注释**（改 C 时顺带发现的不一致：只有 checkpoints 有）。
注释仍是 `db/schema.py` 单点定义，迁移里逐字照抄；新增用例
`test_every_table_has_a_comment` 与列注释那条对称，防将来漏写。

修正后复核：库里六张表齐全（五业务 + `charagent_alembic_version`）、版本号
`0001_core`、外键动作 `CASCADE`、**五张表的表注释与列注释 100% 覆盖**、
`--autogenerate` 零差异；测试 532 / 41 全绿（redis 三例因本机 Redis 未启动而跳过，
与前次 3 passed 的差异属环境）。

**E. 包名 `models/` → `db/`（用户提出，改完复核）**。`CharAgent/model/` 是 LLM 模型层
（ChatModel 协议 + 双适配器），`CharAgent/models/` 是数据层 —— 两个目录名只差一个 s，
光看名字分不出谁是谁。改成 `db/`（里面全是数据库相关的东西：表定义 / 实体 / 仓储），
包内 `models.py` 一并改成 `entities.py`（否则「`models` 包里有个 `models.py`」同样难分辨）。

连带改的：23 个文件的 import 路径、7 个测试文件（`test_models_*` → `test_db_*`）、
pytest marker（`pg_models` → `pg_db`）、文档与注释里的路径。

顺带清掉一处**死代码**：`db/config.py` 里原先有个 `db_dsn()`（原 `models_dsn`），
零调用方、零测试 —— alembic 与快照存储要的都是 `sqlalchemy_url()`，没人需要那个
libpq 关键字形式。已删（与 P0-6「不给没人调的接口」同一条理由）。环境变量名
`MODELS_DSN` / `MODELS_ECHO` **保留不改** —— 改名不带来收益，却会让每个人已有的
`.env` 失效。

复核：532（默认）/ 41（真库）全绿，`--autogenerate` 零差异，ruff 零告警。

**F. 交付后全面审计（2026-09-14，用户要求「最后检查一遍」）**。分机器扫描 + 两个
子代理（覆盖度 / 文档一致性）三路查，逐项核实后修了下面这些。按「真会影响正确性」
到「只是措辞」排序。

#### F1. 修掉的真问题

| 问题 | 后果 | 修法 |
|------|------|------|
| `ModelError` 在 `model/utils/errors.py` 与 `db/errors.py` 里**各有一个**（同名不同类） | `except ModelError` 既兜不住模型层的错也兜不住 db 层的错，而代码看起来毫无问题 | `db` 的基类改名 `DbError` |
| `tool_calls.add` 的 docstring 承诺撞键时报可读的 `DataStoreError`，实现**没包** | 调用方按 docstring 写 `except DataStoreError` 会漏掉这个错 | 补 `IntegrityError` 包装（与 `threads.add` / `runs.add` 一致） |
| `PgDatabase.connect()` 的**借连接/开事务在 try 之外** | 连接失败抛原生 `OperationalError` —— 而「库连不上」恰恰是最常见的故障，docstring 却承诺包成 `DataStoreError` | 整段挪进 try（用 `session is not None` 区分「连都没连上」） |
| `sqlalchemy_url()` 三条路径**返回类型不一致**（显式 DSN 返回字符串） | 调用方得同时防两种形态，`url.drivername` 在字符串上直接 AttributeError | 统一返回 URL 对象 |
| `db/entities.py` 的 `_utcnow()` **是死代码**，而模块 docstring 说「时间戳默认值统一走它」 | 说反了：`__table__` 映射拿不到 Python 侧默认值，实际是「调用方必须自己传」 | 删函数 + 把真实契约写进 docstring |
| `ToolCall` 的 docstring 写「主键是 (run_id, tool_call_id) **两列**」 | 与实现（三列）、与迁移、与用例都矛盾 —— 后来人照它写会撞真实的上游编号重复坑 | 改成三列 |
| `checkpoint/config.py` 宣称「psycopg 的 `connect()` 直接收 URL 对象」 | **实测是错的**（两种形式都报 `'URL' object has no attribute 'encode'`） | 改成实话 + 指向 conftest 的转换函数 |
| `schema.py` 里三处「复合主键的一半/另一半」 | 实际是三列，注释与实现不符 | 改成「三分之一」；库里注释同步 |

#### F2. 补的测试（+34 条，其中新增一个文件）

- **`tests/test_db_config.py`（28 条，全新）**：覆盖审计点名的第一优先项 ——
  `db/config.py` 此前**整文件零覆盖**，而它是 alembic 与运行时唯一的连接串来源，
  配错了不会报错、只会连到别的库上去。测三种来源、两条报错、`echo_enabled()` 真值
  表、密码特殊字符转义、`.env` 引号剥离，以及**两条路径（`db` / `checkpoint`）必须
  给出同一个库** —— 兑现了 docstring 里那句此前并不存在的承诺。
- **`tests/test_db_store.py`（+4 条）**：`add_calls` 成功路径（顺序 + 微秒递增，
  此前「静默乱序」是盲区）、空批次、`conversation_pairs` 的连续两问 / 末尾悬挂问题、
  `limit` 的「取最近但返回正序」。

#### F3. 文档与措辞

- `db/schema.py` 表名常量：删掉零引用的 4 个（只留 `CHECKPOINTS_TABLE_NAME`，它是
  conftest 唯一在用的），并写明「其余四张表的名字在用例里是**故意写字面量**的契约断言」。
- `db/database.py`：类 docstring 里那句「构造期抛 `DataConfigError`」与「构造时不连库」
  自相矛盾，改成实话；`url` 类型补 `URL`；`create_tables` 的「表从哪来」按实现写。
- `checkpoint/postgres.py`：「本模块与 alembic 迁移**都从** `schema.py` 取表定义」
  不准确 —— 已发布的迁移脚本是**冻结的历史，刻意不 import 它**；`delete_thread` 的
  docstring 说「测试在用」但实际没人用（conftest 自己写了容错版），改成实话并注明
  它是 Postgres 专有、不在协议里。
- `checkpoint/base.py`：补一句「协议只声明各实现都有的方法，实现可以另加自己的
  （如 Postgres 的 `delete_thread`），按协议编程的调用方不该依赖」。
- 过时引用：`test_models_store.py` → `test_db_store.py`（两处）、`migrations/env.py`
  → `alembic/env.py`、`loop/` → `agent/`（DESIGN.md 与 01-architecture）、
  `db/README.md` 里 `pyproject.toml` 的位置。
- `alembic.ini`：去掉 `file_template`（它会生成时间戳形式的文件名，与手工写的
  `0001_charagent_core.py` 两套口径并存），改用默认的 `<rev>_<slug>.py`。

#### F4. 复核结论

修完再跑：**560 passed**（默认）/ **45 passed**（真库）/ ruff check + format 零告警 /
`--autogenerate` 零差异 / `alembic current` = `0001_core`。

**F5 第 1 条已修（2026-09-14 用户点名的「未修第 1 条」）**：

- `02-data-model.md` §1 的 ToolCall 字段表补齐 **`message_id`**（它此前只在 §2 出现，
  而它是三列主键之一），并把「三列主键 + 为什么」写进表前；`arguments` 的类型从
  `jsonb` 更正为 `text`（实现存的是原样 JSON 字符串，不预解析）。
- 索引说明：原先散在各表行尾、只有 4 条且与实际不符（threads 的 `user_id` 索引与
  tool_calls 的索引都没写）。改成 **§1 末尾一张「索引一览」汇总表**（6 条，逐条写明
  服务哪种查法），并附一句「加索引要有对应查法」。
- **加了防漂移的用例**：`test_db_schema.py::test_design_doc_index_table_matches_the_code`
  把那张 markdown 表解析出来与 `schema.py` 的 Index 对象逐条比 —— 文档改了代码没改
  （或反之）都会红。这是本轮唯一一处「文档即被测对象」的用例。

#### F5. 审计剩余项（2026-09-14 用户要求一并修完）

三条都处理了：

**① `checkpoint/base.py` 没声明 `ensure_schema`。** 不改协议 —— 它确实是 Postgres
专有的（内存版没有表、Redis 版没有 schema）。改为在协议 docstring 里**列出**这两个
实现专有方法各自是什么、什么时候才需要显式调（`ensure_schema` 只在测试想先把表备好
时调，因为每次存取会自己顺手调一次），把「协议声明的边界在哪」写清楚。

**② 一批接口无直接覆盖。** 补 6 条真库用例（`test_db_store.py`）：

| 用例 | 盯的是什么 |
|------|-----------|
| `test_injected_engine_survives_dispose` | `dispose()` 不关注入的引擎 —— 反例是整个测试套件随后全报「连接已关闭」，而报错处看着无关 |
| `test_thread_list_can_filter_by_owner` | `user_id=` 过滤 + `limit<=0` —— 租户维度测过、**用户维度没测**，漏了就是「同一租户下能看到别人的会话」 |
| `test_thread_duplicate_id_is_rejected_with_a_readable_error` | 与 runs 同一套可读报错 |
| `test_get_run_by_request_id` | 幂等入口的命中 / 未命中两条路径（P1-1 接线靠它） |
| `test_set_status_bypasses_the_state_machine_on_purpose` | 把 `set_status` 与 `try_transition` 的分工**写死在断言里**：前者不做校验是**有意的**，很容易被当成「另一个改状态的方法」拿去用 |
| `test_empty_batches_are_noops` | `add_messages([])` 早返回 |

**③ 文档承诺了不存在的 `python -m CharAgent.cli`。** 那是 issue 10 的交付物，本 issue
没做。不改文档去「补一个 CLI」（那是 issue 10 的范围），而是**标注现状**：
`DESIGN.md` 的运行入口表与 `05-roadmap.md` 的验收行都写明「尚未交付，见 issue 10」，
并指出在此之前跑通链路的入口是 `pytest tests/` 与 `pytest -m integration`。

**顺带补的**：`docs/design/04-test-plan.md` 的 marker 清单只有三个（缺 `pg_db`），
补成四个并加了一张「各 marker 的隔离方式」表（`pg` / `redis` 直连真服务、`pg_db` 在
独立 schema 里干活）。

#### F6. 复核结论（F1~F5 修完后的全量）

- 默认全量：**561 passed / 65 deselected**
- 真库：`pytest -m "pg or pg_db"` → **51 passed**（pg 19 + pg_db 32）
- 真 Redis：**3 passed**
- `--autogenerate` 零差异；`alembic current` = `0001_core`
- Ruff check + format 零告警

至此审计发现的**全部**条目（F1 真问题 8 条、F2 新用例 34 条、F3 文档 9 处、F4/F5 合计 6 条）都已处理，没有遗留。

### 7. 遗留（不属本 issue）

- 运行状态机的**推进者**（谁在什么时候把 running 改成 waiting_tool）→ P1-2；本 issue 交付的是规则与安全落库入口（`try_transition` 用带条件的 UPDATE 做乐观锁）。
- demo 业务表（tickets / escalations / approvals / audit_logs）与幂等表 → P1 迁移（`db/README.md` 写了追加流程）。
- `events` 表（事件溯源 #12）→ P2 迁移。
- 连接池参数调优 / 无状态水平扩展 → P2-10。
