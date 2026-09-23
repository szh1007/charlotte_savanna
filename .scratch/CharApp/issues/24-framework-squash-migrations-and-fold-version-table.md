# 24 · 框架侧：四条迁移压成一条（版本表兼作审计表 + 补 checkpoints 的会话外键）

**Status:** done

**Type:** task

**Blocked by:** 无

**上游:** `CharAgent/db/README.md`「改 schema 的流程」；ticket 17（迁移审计表 `charagent_migrations` 的由来）

## 做什么

三件事一起做，因为它们落在同一份产物上（新的 `0001_core.py`）：

1. **压缩迁移**：删掉 `0001_charagent_core` / `0002_run_usage_breakdown` /
   `0003_migration_audit_log` / `0004_frame_run_linkage`，合成一条
   `0001_core.py`（`revision = "0001_core"`，`down_revision = None`）。理由是**还没
   发布**：那些编号只有本机这一个库用过。
2. **版本表与审计表合一**：`charagent_alembic_version` 不要了，alembic 的版本表就是
   `charagent_migrations`（补 `name` / `history` 两列）。原先两张表说的是同一件事的
   两种读法（当前在哪一版 / 一路怎么走过来），却要时刻保持一致 —— 天然冗余。
3. **补一条外键**：`charagent_checkpoints.thread_id → charagent_threads.thread_id`
   （`ON DELETE CASCADE`）。原先只有注释说它是「分区键」，没有约束保证那个会话真的
   存在。连带一条**行为契约**：写帧之前会话行必须存在。

## 已经替你确认过的事实

- 库里（`charlotte`，7 张表）：`charagent_checkpoints` 29 行（`fix22 / fix22b /
  fix22c` 的验收残留，`run_id` 已被置 NULL）、`charagent_migrations` 4 行、其余空表；
  `charagent_alembic_version` 一行 = `0004_frame_run_linkage`（清空那一刻
  `checkpoints` 实际剩 6 行 —— 中间有 pg 用例往来过，不影响结论）
- **alembic 的版本表是硬约束**（源码级）：必须有一列叫 `version_num`（`ddl/impl.py`
  的 `version_table_impl`），**一行 = 一个 head**（`runtime/migration.py` 的
  `get_current_heads` 把每一行都当一个 head，多行直接报「分叉的 head」），表由
  alembic 在**跑迁移之前**建出来（`run_migrations` 里的 `_ensure_version_table`），
  主键名由它定（`<表名>_pkc`）
- 回调时序：版本行由 alembic 写完**之后**才调 `on_version_apply`（同一步、同一事务）
  —— 所以钩子里的 UPDATE 一定找得到那一行
- 旧审计表 4 行（抄在这儿留档，因为要清库）：
  `0001_core` / `0002_run_usage_breakdown` / `0003_migration_audit_log`
  （三条 `applied_at` = 2026-09-23 05:42:46，`applied_by` = `Lenovo@CHARLOTTE`）、
  `0004_frame_run_linkage`（2026-09-23 10:03:51，同一执行者）
- 全仓**唯一**的 alembic 环境就是 `CharAgent/alembic/`（CharApp / Django 侧没有，
  Django 走 MySQL 不共用库）；库里也只有 `charagent_*` 表，没有别的子项目
- 硬编码 revision 的地方只有三处：`tests/test_db_alembic.py`（head 断言 + 旧审计表
  的「四行 + 标题 + 下标定位的 applied_at」）、`alembic.ini` 的命名注释、
  `db/README.md` 的「既有迁移」段（它连 0004 都没提，本来就过期了）
- `tests/conftest.py` 的 `pg_thread_id` 是**三个 saver 实现共用**的（内存 / Redis /
  PG 参数化）—— 加外键之后只有 PG 那组需要会话行，所以建行要按 `pg` 标记区分
- `test_db_alembic.py` 的 `_reflect()` 会把版本表从反射结果里去掉（断言「表齐全」
  时的假阳性），所以版本表合并进来不会影响那条用例

## 具体任务

1. 写 `alembic/versions/0001_core.py`：五张业务表按 `db/schema.py` 的声明序**重排**
   （列与表都按逻辑序），环的那条外键（`runs.last_checkpoint_id`）留到两张表都建完
   再 ALTER；给版本表补 `name` / `history` 两列 + 表注释
2. `alembic/env.py`：`VERSION_TABLE = "charagent_migrations"`；钩子从
   INSERT/DELETE 改成「一条 UPDATE 改写标题 + 往 `history` 追加一条」
3. `db/schema.py`：摘掉 `migrations` 表（连同 `ALL_TABLES`）；给 `checkpoints.thread_id`
   补外键与注释
4. 测试：`test_db_alembic.py` 三处断言、`test_db_schema.py`（六张 → 五张 + 新外键）、
   `conftest.py` 的 `pg_thread_id`（标 `pg` 的用例建会话行，收尾靠 CASCADE 删）
5. 清库重建：**先 dump 基线** → `DROP` 七张表 → `alembic upgrade head` → 再 dump →
   diff（应当只差那条新外键与物理列序）
6. 文档：`db/README.md`（「既有迁移」与「迁移历史看哪里」重写 + 写帧前的会话行契约）、
   `alembic.ini` 的命名注释、`checkpoint/postgres.py` 的 `save()` docstring、本篇票、
   ADR `CharApp/docs/adr/0006-migration-history-is-squashed-and-folded-into-one-table.md`

## 验收

- [x] 重建后的结构与重建前逐字等价，**只差**：新外键、物理列序、版本表的形状（用
      pg 结构 dump 做前后 diff 证明）
- [x] `alembic check` 零差异；`pytest -m pg_db` 全绿（含「迁移结果与表定义逐列一致」
      那条）
- [x] 版本表那一行同时带着审计信息：`version_num = 0001_core`、`name` = 脚本 docstring
      首行、`history` 一条 `{from: null, to: 0001_core, at, by}`（时刻与执行者是真值）
- [x] `downgrade base` → `upgrade head` 往返干净（含钩子在「两列已被拆掉」那一步安静
      跳过，不让回退失败）
- [x] `pg` 标记的用例全绿（帧能写进有会话行的库；内存 / Redis 那几组参数不被牵连）
- [x] 框架全量 + CharApp 全量 + `ruff` 干净

## 备注

- **不碰**：`.scratch` 里 17 / 20 / 22 三张旧票的历史记录（那是某天的事实）；根
  `CLAUDE.md` / `README.md`（按约定 L4 收尾前不动）
- **放弃的能力**：`downgrade base` 会连 `history` 一起清掉（alembic 删唯一那行）；
  「上过哪几版」在压缩前的旧表里是累积的，合并后靠 `history` 字典的键。要留档先导出
- **只记「最近一次」**：`history` 的值是「这一版最近一次成为当前版」的时刻与执行者，
  回退会覆盖它 —— 「首次上线时刻」不留，同一版被上过两次也分不清
- **没做**：`checkpoints.thread_id` 的替代方案（可空 + SET NULL，与 `run_id` 的哲学
  一致）—— 会让「孤儿帧」无人认领，且分区键语义被弱化，不取
- **一处只有临时探针验过的分支**：「回退一步时改写退到的那一版那个键」现在只有一条
  迁移，真机走不出来 —— 用临时迁移目录（探针 0002）验过（见「六」）。等 0002 真落地
  时它会被常规用例覆盖，届时把探针那条证据换成正式用例

---

## 实际开发情况 2026-09-23

### 一、定案（先 grill 后开工，三轮问答）

| 问 | 结论 |
|---|---|
| 压缩的边界 | **要夹带**结构调整（不是「只压缩」）：补 `checkpoints.thread_id` 外键 |
| 新脚本的写法 | 按 `db/schema.py` 的声明序**重排**（不内联旧文本） |
| 清空范围 | 全部 drop 重建；`charagent_alembic_version` 不再要，用 `charagent_migrations` 代替 |
| 版本表的新形状 | `version_num`（alembic 写）+ `name`（当前版标题）+ `history JSONB`（`{from, to, at, by}` 一步一条）——**不要** `applied_at` / `applied_by` 两个独立的列（它们是 `history` 最后一条的投影，同一件事存两处） |
| 钩子的记法 | 靠 alembic 同一步自然记上（不保留旧设计的「补记留空」） |
| 测试里的 head | 保留硬编码，改成 `0001_core` |
| 命名 | 文件名 = revision = `0001_core`（顺带填平旧的「文件名与 revision 不一致」） |
| 文档 | 开本篇 + 一篇 ADR（放 `CharApp/docs/adr/0006`） |

### 二、三处不能照抄 `db/schema.py` 的地方（实施时才撞到的）

1. **环的那条外键**：`runs.last_checkpoint_id` 建表时 `checkpoints` 还不存在 ——
   留到两张表都建完再 `ALTER`（`op.create_foreign_key`），与 0004 的做法一致.
2. **版本表不能自己 create**：alembic 在跑迁移**之前**就把 `charagent_migrations`
   建好了（只带 `version_num`），所以 `0001_core` 只 `add_column` 两列；自己
   `create_table` 会撞「关系已存在」.
3. **两列必须扛得住「只写 version_num」的 INSERT**：`name` 给 `server_default=""`、
   `history` 给 `server_default="[]"`，真值由钩子在同一步回填（否则 alembic 那条
   INSERT 会因 NOT NULL 失败）.

### 三、钩子的新写法

| | 旧 | 新 |
|---|---|---|
| 表 | `charagent_alembic_version`（一行 head）+ `charagent_migrations`（累积） | `charagent_migrations`（一行 = head + 它这一路的 `history`） |
| 升级 | INSERT 一行 | UPDATE：`name` 换成新版的标题、`history` 追加 `{from, to, at, by}` |
| 回退 | DELETE 一行 | 同上（`from` / `to` 自然表达方向） |
| 「表还不存在」的跳过 | `to_regclass` 判表 | `information_schema.columns` 判**列**（`downgrade base` 最后一步两列已被拆掉） |

`from` / `to` 取 `step.source_revision_ids` / `destination_revision_ids`（两个方向都
准确，且「走完之后停在哪一版」永远等于 `destination` —— 于是那一行始终说着「现在
这里是哪一版」）.

### 四、碰过的文件

| 文件 | 改动 |
|---|---|
| `CharAgent/alembic/versions/0001_core.py` | 新增（五张表重排 + 版本表两列 + 环外键 + 新会话外键；含 downgrade） |
| `CharAgent/alembic/versions/000{1,2,3,4}_*.py` | 删除（四条合成一条） |
| `CharAgent/alembic/env.py` | `VERSION_TABLE` 改名；钩子改 UPDATE + 追 history；守列改为判列；模块 docstring 加第 4 件事 |
| `CharAgent/db/schema.py` | 摘掉 `migrations` 表与 `ALL_TABLES` 里的它；`checkpoints.thread_id` 补外键与注释；模块 docstring 说明「版本表为什么不在这里」 |
| `CharAgent/tests/test_db_alembic.py` | 版本表常量、两处 head 断言、审计断言改成「一行 + history 一条」 |
| `CharAgent/tests/test_db_schema.py` | 六张 → 五张；`test_foreign_key_delete_actions` 补 `checkpoints.thread_id` |
| `CharAgent/tests/conftest.py` | `pg_thread_id` 按 `pg` 标记建会话行；收尾删会话行（帧靠 CASCADE 走） |
| `CharAgent/db/README.md` | 「既有迁移」「迁移历史看哪里」重写；补写帧前会话行的契约 |
| `CharAgent/alembic.ini` | 命名注释指向 `0001_core.py` |
| `CharAgent/checkpoint/postgres.py` | `save()` docstring 写明「写帧前会话行必须存在」 |
| `CharApp/docs/adr/0006-*.md` | 新增（压缩 + 两表合一） |

### 五、验证到哪一步

清空 → 重建 → 逐字比对（`charlotte` 库，2026-09-23）：

| 步 | 命令 / 动作 | 结果 |
|---|---|---|
| 1 | `DROP` 七张表（drop 前留了行数清单） | public 里 `charagent_*` 表清空 |
| 2 | `alembic upgrade head` | `Running upgrade  -> 0001_core`；**第一次跑报错**（见下），修好后一次通过 |
| 3 | 结构 dump 前后 diff | 差异**只有预期四类**：`charagent_alembic_version` 消失、`charagent_migrations` 换形状、新外键 `fk_charagent_checkpoints_thread_id_charagent_threads`、两张表的物理列序回到代码声明序。五张业务表的列 / 类型 / 可空 / 默认 / 索引 / 其余外键 / 注释**逐字未变** |
| 4 | 版本表那一行 | 行数 1：`version_num=0001_core`、`name=五实体核心表 + 版本表的审计两列: 压缩后的唯一起点`、`history=[{"from": null, "to": "0001_core", "at": "2026-09-23T11:17:28+08:00", "by": "Lenovo@CHARLOTTE"}]` |
| 5 | `alembic check` | `No new upgrade operations detected.`（零差异） |
| 6 | `downgrade base` → `upgrade head` | 回退后只剩 `charagent_migrations`（列回到只剩 `version_num`，0 行，钩子在那一步安静跳过）；再升级回到 6 张表 + 一行新 history |
| 7 | `pytest -m "pg or pg_db or redis"` | **65 passed**（真库 / 真 Redis） |
| 8 | `pytest CharAgent` / `pytest CharApp` / `ruff` | **995 passed / 76 deselected** · **198 passed** · `check` 与 `format --check` 干净（`995` 比开工前多 1：八 里补的那条 CLI 装配用例） |

> 上面第 3～8 步在「六」那两处改动之后**整体复跑过一遍**（含库全量重建与 `alembic
> check` 零差异），并按「八」全库扫描的结论又复跑过一遍 —— 表里是最后一次的数字。

**第 2 步第一次跑撞到的 bug**：`jsonb_build_object('from', :from_revision, ...)` 在
「从空库起」那一步绑的是 NULL，而它的形参是 variadic `any` —— PG 判不出类型，报
`IndeterminateDatatype: 无法确定参数 $2 的数据类型`. 修法是把三个文本参数显式
`cast(:x as text)`（NULL 于是变成一个 jsonb null）. 这一条**只有真机跑得出来**：离线
用例用的是假连接，看不出 PG 的类型推断. 顺带确认了好的一面 —— 失败时整个 `upgrade`
连同版本表的建表一起回滚，库里干净得不留半截（PG 的事务性 DDL）.

**关于那条外键的「真机证据」**：没有单独再跑一遍 CharApp 服务，因为**早就跑过了** ——
ticket 22 那次真机验收里 `charagent_runs.thread_id` 就已经是指向 `charagent_threads`
的外键（CASCADE），而当时真的落下了运行行与帧。也就是说「会话行先于运行行、运行行先于
帧」这条顺序在真实链路里已经成立，本次只是把同样的约束补到帧自己身上。

### 六、开工后按你的要求改的两处（2026-09-23 晚）

| 改动 | 内容 |
|---|---|
| **列序交换** | `charagent_checkpoints` 的 `run_id` 与 `loop_id` 换位 → `checkpoint_id → thread_id → run_id → loop_id → …`（两张列外键排在前面，先看到「它属于谁」）。`db/schema.py` 与 `0001_core.py` 同步改，库全量重建 |
| **history 从数组改字典** | 键 = 迁移编号，值 = 这一版**最近一次成为当前版**时的 `{from, at, by}`（`from` 为 null = 从空库起）。回退时改写**退到的那一版**那个键 —— 代价是那一版的「首次上线时刻」被覆盖，换来回退也留痕（只记首次的话，一次 downgrade 之后这一行就在撒谎）。**没有 `applied_at` / `applied_by` 两个独立列**：它们是 `history` 里当前那条的投影，同一件事存两处 |

重建后真机复验：

```
version_num: 0001_core
name       : 五实体核心表 + 版本表的审计两列: 压缩后的唯一起点
history    : {"0001_core": {"at": "2026-09-23T11:40:32+08:00", "by": "Lenovo@CHARLOTTE", "from": null}}
checkpoints 物理列序: checkpoint_id, thread_id, run_id, loop_id, turn_number, schema_version, state, metadata, parent_id, created_at
```

**「回退改写目标版的键」这条分支真机走不出来**（现在只有一条迁移），于是拿一份**临时
迁移目录**（真 `env.py` + 真 `0001_core` + 一条探针 `0002_probe`，跑在测试 schema 里、
完事整个删掉）走了三步：

| 步 | 结果 |
|---|---|
| `upgrade head`（两版） | `history = {"0001_core": {from: null, …}, "0002_probe": {from: "0001_core", …}}`；`name` = 当前那版的标题 |
| `downgrade 0001_core` | `version_num` 回到 0001、`name` 换成 0001 的标题、**`history["0001_core"]` 被改写成 `{from: "0002_probe", at: 刚才, by: …}`**，`0002_probe` 那个键留着不动 |
| `downgrade base` | 两列被 0001_core 的 downgrade 拆掉（只剩 `version_num`），钩子在「列已不在」那一步安静跳过 —— 回退没失败 |

### 七、代码审查改了什么（Standards / Spec 两轴各起一个 sub-agent，2026-09-23）

**Standards 轴**

- **硬违规**：`conftest.py` 的 `_delete_thread_row` 把 `charagent_threads` 抄成字面量，
  而同一段的 `_ensure_thread_row` 用的是 Table 对象 —— 违反 `schema.py` 定下的「表名
  从 Table 对象上取，不许再抄一遍」→ 改成 `f"DELETE FROM {threads.name} …"`。
- **判断题（重复代码）**：`0001_core.py` 里那句表注释在 `create_table_comment` 与
  `drop_table_comment(existing_comment=…)` 各写了一整遍。查了 alembic 源码：
  `existing_comment` 只在 autogenerate 比差异时用，运行时**不参与**（`impl.
  drop_table_comment` 只发 `COMMENT ON TABLE … IS NULL`），而这张表两边比对都排除在外
  → 直接不传这个参数，重复从根上没了。改完真机重跑往返：回退后表注释确实被清成 NULL、
  再升级注释回来。

**Spec 轴**

- **ADR 0006 压根不在盘上**（真发现）：那次 Write 报了成功，但目录 mtime 停在 0005 那
  一刻、`git status` 里也没有它 —— 文件从没落地。已重写并用 `ls` + `git status` 复核。
- **`_APPEND_HISTORY` 把表名写死在 SQL 里**，而守列判断用的是 `VERSION_TABLE` —— 与
  README 新写的「名字只在那一处定义」矛盾。改名之后会出现「守门放行、UPDATE 命中 0 行、
  `name`/`history` 静默不写」这种最难查的组合 → 改成 f-string 从 `VERSION_TABLE` 取。
- **`0001_core.py` docstring 的第 3 条只举了 `runs` 一张表**，漏了 `checkpoints`（它的
  `run_id` 也是 0004 追加到表尾的）→ 改写为「两张表的物理列序」。

**收尾统一说法**（2026-09-23，你点的）：这张表一律叫「**版本表**（兼作审计表）」。此前
散着「审计表」「版本表与审计表」几种写法，改了 5 处（`db/entities.py`、`db/README.md`、
`test_db_alembic.py` ×3、`alembic/env.py`）；统一之后 `grep 审计表` 只剩三种正当用法：
「兼作审计表」、历史对比（旧形状叫「累积审计表」）、历史事实（清库前那张表那 4 行）。

### 八、全库扫描（2026-09-23，你点的：多余代码 / 失效代码 / 不兼容代码）

两路并行扫（CharAgent 侧 / CharApp + 仓库外围）。**真正的不兼容只有一条** —— 其余是过期说法与两处待办票里的失效指令。

| 级别 | 位置 | 处理 |
|---|---|---|
| **不兼容** | `client/app.py`：`--backend postgres` 现在第一帧就撞外键（CLI 里没有记录层，没人建会话行；`test_client_app.py` 只跑 memory / redis，所以没人先撞上） | 按你的决定**接上记录层**（`_recorder_for`：只有 Postgres 快照后端挂记录员，身份写死 `cli`）。真机验：一句问话 → 会话行 1 / 运行行 1 / 消息 2 / 帧 1（帧带 `run_id`），收尾删会话行 CASCADE 干净；补了一条装配用例钉住「只有 postgres 挂记录员」 |
| **多余代码** | `alembic/script.py.mako` 把 docstring 头（`Revision ID` / `Revises` / `Create Date`）印了两遍 —— 而首段正是 `charagent_migrations.name` 的产出地 | 删掉重复那三行 + 加一句说明 |
| 契约没写到调用方看得见的地方 | `server/app.py` 的 `database` 说明 · `server/__init__.py` 的装配例子 · `checkpoint/__init__.py` 的最小例子 · `agent/loop.py::_save_checkpoint` · `client/session.py::_begin_run` 的括号 · `checkpoint/postgres.py::delete_thread` 的「级联删除属后续阶段」 | 各处补一句。`delete_thread` **保留**（按「之前就存在的死代码不删」的规矩），只把说法改对：级联已由外键兜住，它留给「只清快照、留着会话」的场合 |
| 过期说法（CharApp 侧） | `docs/PLAN.md` 的「已记录的四条 ADR」（实际六条，0005 也漏了）· `minimall/service.py` 的「不记账也能照常问答」· `minimall/server.py` 的「买家的问答不受影响」 | 都补了限定：**postgres 快照后端 + `database=None` 是条被堵死的组合** |
| 待办票里的失效指令（按你的决定：只改指令、不改历史） | ticket 20 引的 `0001_charagent_core.py` 与行号 → `0001_core.py:1-54 / :64-68 / :623-659`；软删理由那条补「帧也会被 CASCADE 带走」；ticket 21 计划的两个 ADR 号 0005 / 0006 已被占 → **0007 / 0008**，「现有四条」→ 六条 | 改 |
| 看过没问题（一句话） | 旧表名 / 旧列名 / 旧钩子常量全仓 0 处 · 无 `SELECT *`、无按列位置读 checkpoints（唯一的位置解包用的是显式列清单）· `history` 只有三处读法且都是 dict · 测试替身不为审计表建模 · 除 `delete_thread` 外无孤儿常量 · CharApp 零真库用例、替身只映射三张业务表 → 不会红 | 不动 |

**顺带记一条与本改动无关的坑**：`pytest CharAgent CharApp` 一条命令跑两个包会在 collection 阶段失败 —— 两个包各有顶层 `conftest.py`，同名模块相撞（`CharAgent/tests/*` 的 `from conftest import …` 会解析到 `CharApp/tests/conftest.py`）。一直以来的跑法是分开跑，知道就好。

> 记录人：Claude Code (charlotte) · 2026-09-23
