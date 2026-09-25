# CharAgent / db —— 数据模型与数据访问层

> 数据层的交付说明与**维护手册**。

> 目录叫 `db/` 而不是 `models/`：与 `CharAgent/model/`（LLM 模型层 —— ChatModel 协议
> 与双适配器）名字太近，两个都叫 model 会分不清谁是谁。这里的东西全是数据库相关
> 的（表定义 / 实体 / 仓储），`db` 一眼到位。

## 这一层解决什么问题

P0 前六项交付后，运行时能跑，但**业务数据无处落库**：谁开的会话、这次运行跑成
什么样、用户问了什么、模型答了什么、调了哪些工具 —— 只在内存与 checkpoint 快照
里（快照只存「接着跑要用的」，不回答「查得到、看得到」）。

本层把五个实体定死（thread / run / message / tool_call / checkpoint），用
alembic 把 Postgres 结构纳入版本管理，并提供五个取数口（仓储）。加上第六张表
`charagent_idempotency_keys`（幂等登记簿，**没有实体** —— 它没有「业务视角」的
那一半，只有「认领」一个动作，见 `schema.py` 与 `repositories/idempotency.py`）。

## 文件分工

| 文件 | 管什么 |
|------|--------|
| `schema.py` | **表定义唯一来源**（六张 `Table`；迁移与快照存储都从这里取） |
| `entities.py` | 五个实体 + 四个状态枚举（`Thread` / `Run` / `Message` / `ToolCall` / `CheckpointRow`） |
| `state.py` | 运行状态机规则（合法迁移表 + 结束原因映射） |
| `conversation.py` | 会话消息分层（哪几条给前端看） |
| `database.py` | 连库与事务（`PgDatabase`：同步引擎 + `asyncio.to_thread`） |
| `config.py` | 连接配置（环境变量 → 连接串） |
| `errors.py` | 三类错误（配置错 / 状态迁移非法 / 库出错） |
| `repositories/` | 五个取数口（会话 / 运行 / 消息 / 工具调用 / 幂等登记） |
| `../alembic/` | 迁移脚本（结构变更的历史，**只追加不改写**；2026-09-23 发布前压缩过一次，见下文「既有迁移」） |

> 迁移目录在 `CharAgent/alembic/`（**不在 `db/` 里面**）—— 这是 alembic 的
> 惯例布局（与 `pytest.ini` / `alembic.ini` 同级），因为它管的是整个项目的
> 库结构而不只是 `db/` 这一个包：将来 demo 表、events 表也走同一套迁移。
> 入口是 `alembic.ini` 的 `script_location = %(here)s/alembic`。

## 事务模型：一次 `connect()` = 一次事务

```python
db = PgDatabase()                      # 从环境变量读连接串
async with db.connect() as session:    # 进入事务
    session.execute(...)               # 正常退出自动提交
# 中途抛异常 -> 自动回滚，不留半截数据
```

需要多步同生共死时（「建运行 + 写用户消息」），把它们放进**同一个** `connect()`
里 —— 否则中间挂掉会留下「运行建好了但消息没写进去」这种自相矛盾的数据。

## 会话消息分层（本层最容易搞错的地方）

真正的 wire 消息历史比「一问一答」多得多（system 续写指令、tool 回填、带
tool_calls 的中间轮），而且**没有地方存得不对就是正确性问题**：把模型编出来的
「用户说…」渲染成用户真的说过，客户端与服务端会同时被判 bug。

| 表 | 存什么 |
|----|--------|
| `charagent_messages`（`hidden=False`） | 给前端看的：用户真发的问题 + 最终答复正文 |
| `charagent_checkpoints.state.messages` | 完整 wire 历史（恢复靠它；`hidden=True` 的行也落在这里） |

两条铁律（都在 `conversation.py` 里，有用例钉住）：

1. **只有真由用户输入产生的消息才是 `user`** —— 模型在回复里虚构的「用户说…」
   角色是 `assistant`，绝不能当 `user` 存。
2. **最终答复取 `LoopResult.content`，不能抄 `messages[-1]["content"]`** ——
   截断续写（CONTINUE）时前者是跨段拼合结果，后者只是尾段，抄尾段等于把答案
   拦腰截断交给用户。

## 改 schema 的流程（只追加，不改写历史）

```bash
# 1. 改 db/schema.py 里的表定义（唯一来源）
# 2. 让 alembic 与库比对，自动写出迁移脚本
alembic -c CharAgent/alembic.ini revision --autogenerate -m "add xxx table"
# 3. 看一眼生成的脚本（自动生成的偶尔会多带私货：与本次无关的差异）
# 4. 应用到库里
alembic -c CharAgent/alembic.ini upgrade head
# 5. 跑一次验证（迁移结果必须与表定义逐列一致）
pytest -m pg_db
```

**为什么已发布的迁移脚本不能改**：它是「历史」。改它会让「在老库上跑这条迁移」
与「在新库上跑同一条迁移」建出不同的表，版本号从此失去意义。要改结构就加**新的**
迁移。（唯一一次例外是 2026-09-23 的压缩：那时还没发布，只有本机这一个库用过那些
编号，压掉不欠谁的 —— 发布之后不再有这种例外。）

**既有迁移**（四条）：

| 编号 | 做了什么 | 备注 |
|------|---------|------|
| `0001_core` | 五张业务表 + 版本表的两列审计信息 | 上面那次压缩的产物：此前是四条（`0001_core` / `0002_run_usage_breakdown` / `0003_migration_audit_log` / `0004_frame_run_linkage`），合并后的脚本长什么样、与当时那条 head 差在哪三处，都写在脚本的 docstring 里 |
| `0002_thread_management` | `charagent_threads` 补两列（`pinned_at` / `deleted_at`） | **压缩之后的第一条增量迁移** —— 「只追加不改写」这条规矩从它开始真正被执行（ticket 20 的会话管理动作） |
| `0003_run_cost_columns` | `charagent_runs` 加一列（`total_cost_detail`）并把 `total_cost` 改成可空 | 成本口径改成「收尾算好写死」（ticket 28，取舍见 ADR-0018）；改成可空是为了把「没算出来」（NULL）与「真的花了 0 元」分开 |
| `0004_idempotency_keys` | 新建 `charagent_idempotency_keys`（幂等登记簿） | HITL 的挂起-恢复跨进程，进程内 dict 挡不住重放（ticket 32，依据 ADR-0017）；**只加一张表，不碰任何既有表** |

四条都是**手写**的：那份 `op.create_table` / `op.add_column` 是逐列核对过的结果，
而纪律的真正保险不是 `--autogenerate` 这个动作，是 `pytest -m pg_db` 里那条
「迁移结果与表定义逐列零差异」的比对 —— 手写脚本一样要过它。

**迁移历史看哪里**：一张表 `charagent_migrations` 答完 —— ticket 24 起它就是
alembic 的版本表，兼作审计表：

```sql
-- 现在在哪一版、这一版是什么时候由谁成为当前版的
select version_num, name,
       history -> version_num ->> 'at' as at,
       history -> version_num ->> 'by' as by,
       history -> version_num ->> 'from' as from_rev
from charagent_migrations;

-- 上过哪几版、各是什么时候由谁成为当前版的 (按时间排)
select key as revision, value ->> 'at' as at,
       value ->> 'by' as by, value ->> 'from' as from_rev
from charagent_migrations, jsonb_each(history) as e(key, value)
order by at;
```

**为什么只有一行**：alembic 拿 `version_num` 当 head，多一行就会被当成**分叉的
head**（后续 `upgrade` 直接报错），所以它天生不是「一行一条迁移」的应用日志。历史
因此记在**同一行的 `history` 字典**里：键 = 迁移编号，值 = 这一版**最近一次成为当前
版**时的 `{from, at, by}`（`from` 为 null = 从空库起的那一步），由 `alembic/env.py`
的 `on_version_apply` 钩子写（**与迁移同一个事务**：要么都成要么都不成）。升级记的
是「从哪一版上来的」，回退记的是「从哪一版退回来的」—— **回退改写的是退到的那一版
那个键**，所以那一版的「首次上线时刻」会被覆盖：换来的是回退也留痕（只记首次的话，
一次 `downgrade` 之后这一行就在撒谎）。

**回退到 base 会把这一行（连同 `history`）清掉**（alembic 删的唯一那行；它在线模式
从不删版本表本身）—— 要留档就先导出。

**这张表为什么叫这个名字、为什么不写在 `db/schema.py` 里**：名字不能用 alembic
默认的 `alembic_version`（通名会撞，撞了的后果是「别人的迁移读到我们的版本号，
于是该建的表被跳过」，与业务表同一个理由），在 `alembic/env.py` 的
`VERSION_TABLE` 里定义、只那一处；它也不进 `schema.py` 的表定义 —— `version_num`
与主键名都由 alembic 定，我们声明一份只会与它打架，而 `alembic check` 会按
`version_table` 把它从代码侧与库侧**两边**排除，不声明照样零差异。`name` /
`history` 两列由 `0001_core` 补上。

**若 autogenerate 冒出一堆与本次无关的差异**（尤其是 `charagent_checkpoints` 的
类型或注释），说明代码与库已经漂移了 —— 先查清原因，别把那串差异一起提交。

## 追加路径（结构已预留）

| 阶段 | 加什么 | 怎么加 |
|------|--------|--------|
| **P1** | ~~幂等表 `idempotency_keys`（**仅此一张**）~~ | **已建**（2026-09-25，ticket 32）：`0004_idempotency_keys` → 表名 `charagent_idempotency_keys`。它挡什么、过期怎么算、为什么不做后台清理，见 `schema.py` 那张表的注释与 `repositories/idempotency.py` 的模块 docstring |

> **2026-09-18**：原列在本行的 `tickets` / `escalations` / `approvals` / `audit_logs` **四张业务表已移出框架**（分层剥离）—— 它们归业务侧独立维护，走自己的迁移链与版本表，与本文的追加流程无关。框架侧只留 `idempotency_keys`（请求幂等属运行时能力）。
| **P2** | `events` 表（事件溯源 #12）、`memories` 表、`cost_entries` 表 | 自增下一号迁移 |

两条约定：

- **表名一律带 `charagent_` 前缀** —— 本项目各子项目共用同一个 Postgres 库，
  而 `checkpoints` 已经真撞过一次名（与 `langgraph-checkpoint-postgres` 同名，
  报错信息与真因毫无关系）;
- **每列都写 `comment=`** —— 注释会随建表写进库（`COMMENT ON`），是运维排查时的
  文档。有用例强制这一条（`test_db_schema.py::test_every_column_has_a_comment`）。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `CHARAGENT_DB_DSN` | 由 `PGSQL_*` 拼 | 专用连接串（想指到别的库时用它覆盖） |
| `CHARAGENT_DB_ECHO` | 关 | 是否把执行的 SQL 打到日志（排查时开） |
| `PGSQL_USERNAME` / `PGSQL_PASSWORD` / `PGSQL_HOST` / `PGSQL_PORT` / `PGSQL_NAME` | 无 | 共用库配置（与根 `.env` 同名） |

## 测试

```bash
pytest tests/test_db*.py                       # 离线：形状 / 状态机 / 分层 / 映射
pytest -m pg_db                                # 真库：仓储 + alembic 迁移（隔离 schema）
```

真库用例在**独立 schema**（`charagent_test` / `charagent_alembic_test`）里干活，
跑完整个删掉 —— 开发库的 `public` 一个字节都不动。

## 与 checkpoint 包的关系

`checkpoint/` 是**快照存储**（断点续跑 / 翻历史 / time-travel 一整套语义），它的
Postgres 实现与本层共用同一个连接层（`PgDatabase`）与同一份表定义（`schema.py`），
但**不共用仓储** —— 快照那张表的读写归 `checkpoint/postgres.py`，本层不另造一个
功能重叠的入口（两个入口改同一张表迟早会出现语义冲突）。

**一条跨层的契约**（ticket 24 起）：`charagent_checkpoints.thread_id` 是指向
`charagent_threads` 的外键（`ON DELETE CASCADE`），所以**写帧之前会话行必须已经
存在**。快照存储自己不知道 `tenant_id` / `user_id`（本层 `threads` 的两个 NOT NULL
列），没法替调用方补建：配了记录层（`RunRecorder.begin`）的进程由记录员建，直接
拿 `PostgresCheckpointSaver` 单独用的调用方要先自己建一行。反过来说，删掉一个会话
会**连它的全部帧一起删掉**。

依赖方向单向：`checkpoint → db`，`db` 不知道 checkpoint 存在。
