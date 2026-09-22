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
alembic 把 Postgres 结构纳入版本管理，并提供四个取数口（仓储）。

## 文件分工

| 文件 | 管什么 |
|------|--------|
| `schema.py` | **表定义唯一来源**（五张 `Table`；迁移与快照存储都从这里取） |
| `entities.py` | 五个实体 + 四个状态枚举（`Thread` / `Run` / `Message` / `ToolCall` / `CheckpointRow`） |
| `state.py` | 运行状态机规则（合法迁移表 + 结束原因映射） |
| `conversation.py` | 会话消息分层（哪几条给前端看） |
| `database.py` | 连库与事务（`PgDatabase`：同步引擎 + `asyncio.to_thread`） |
| `config.py` | 连接配置（环境变量 → 连接串） |
| `errors.py` | 三类错误（配置错 / 状态迁移非法 / 库出错） |
| `repositories/` | 四个取数口（会话 / 运行 / 消息 / 工具调用） |
| `../alembic/` | 迁移脚本（结构变更的历史，**只追加不改写**） |

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
迁移。

**既有迁移**：`0001_core`（五实体建表）、`0002_run_usage_breakdown`（runs 加用量
分解五列）、`0003_migration_audit_log`（建迁移审计表 + 补记前两条）。后两条为
**手写**：`0002` 是「加几列」这种一眼看得清的动作，手写比 autogenerate 少一轮核对；
而那道纪律的真正保险不是 `--autogenerate` 这个动作，是 `pytest -m pg_db` 里那条
「迁移结果与表定义逐列零差异」的比对 —— 手写脚本一样要过它。

**迁移历史看哪里**（`alembic_version` 只有一行，那是设计不是丢数据）：
`charagent_alembic_version` 记的是**当前 head** —— alembic 靠它判断还该跑哪些迁移，
多行会被当成**分叉的 head**（后续 `upgrade` 直接报错），所以它天生不是应用日志。
「哪条迁移什么时候上的」查 `charagent_migrations`：

```sql
select revision, name, applied_at, applied_by
from charagent_migrations order by applied_at nulls first;
```

那张表由 `alembic/env.py` 的 `on_version_apply` 钩子写（**与迁移同一个事务**：要么
都成要么都不成），应用插一行、回退删一行。建表之前的那两条（0001 / 0002）由 0003
补记，`applied_at` / `applied_by` 留 NULL —— 它们在**任何**库里都发生在建表之前，
无从考证，编一个时间比留空更糟（见 0003 的 docstring）。要看完整的迁移链（含未来
还没跑的）仍然用命令：`alembic -c CharAgent/alembic.ini history`。

**版本表叫 `charagent_alembic_version`**（不是 alembic 默认的 `alembic_version`）：
与五张业务表同一条理由 —— 各子项目共用同一个 PG 库，通名的版本表会撞，而撞了的
后果是「别人的迁移读到我们的版本号，于是该建的表被跳过」。名字在 `alembic/env.py`
的 `VERSION_TABLE` 里定义，只那一处。

**若 autogenerate 冒出一堆与本次无关的差异**（尤其是 `charagent_checkpoints` 的
类型或注释），说明代码与库已经漂移了 —— 先查清原因，别把那串差异一起提交。

## P1 / P2 的追加路径（结构已预留）

| 阶段 | 加什么 | 怎么加 |
|------|--------|--------|
| **P1** | 幂等表 `idempotency_keys`（**仅此一张**） | 在 `schema.py` 里按同样风格加 `Table`（`charagent_` 前缀），自增下一号迁移（`000N_<slug>`，现有 head 见 `alembic/versions/`，或查 `charagent_migrations`） |

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

依赖方向单向：`checkpoint → db`，`db` 不知道 checkpoint 存在。
