# C16 · rag_text2sql 安全与健壮（只读白名单 + LIMIT + 超时 + 重试）

**Status:** done

**Type:** hardening

**Blocked by:** —（C15 已 done：基线报告 `app/eval/reports/baseline.json` 就是本票「不把结果改坏」的前后对照基准；跑批器里已有一层同源护栏（`app/eval/guard.py`：只读白名单 + LIMIT 包裹 + 只读事务），本票把它提升到节点层时可参照/抽取，勿重复造）

**上游:** `app/agent/nodes/_7_validate_sql.py` / `_9_execute_sql.py`；`app/repositories/mysql/dw.py`；`.scratch/Charlotte/PLAN.md` §2 组 C

## 现状（2026-09-30 读码，四条都是硬事实）

| 事实 | 后果 |
|---|---|
| **唯一的护栏是 prompt 里的一句话**（`prompts/generate_sql.prompt:20`「只能用于查询」） | LLM 一旦生成 `DROP` / `TRUNCATE` 就**真的执行** |
| **无强制 LIMIT** | 大结果集全拉回应用层 |
| **无超时** | 一条慢查询挂住整个请求 |
| **无重试** | 全仓 grep `timeout|retry|backoff|tenacity` **零命中**；LLM 一次 502 就整图失败（日志里 03:11/03:12/03:16 多次 `Error code: 502` 直接中断） |

**唯一缓解着的一条**（要如实写进文档）：全程**没有任何 `commit()`**，所以普通 DML 会随 session 关闭回滚。**但 MySQL 的 DDL 是隐式提交** —— 生成 `DROP` / `TRUNCATE` 就真生效。**这条差别是面试官最容易问的点，别答错。**

> **C15 跑批量出来的现场位置（2026-10-05，117 次真跑）**：2 次运行失败全是 240s 超时，
> 且**都卡在 `recall_value` 的那次 LLM 调用**（`_2_3_recall_value.py:34`）—— 该节点两条
> 日志一条未打出，而并行的列召回 / 指标召回都在几秒内完成；超时前的最后一条日志时间戳
> 与下一次超时窗口完全对得上（`logs/app.log` 可复核）。
> **2026-10-06 复跑结论**：同一题复跑 6 次全部成功（8.6~17.1s），说明是**上游偶发挂起**
> 而非确定性输入问题；客户端读超时 600s、`max_retries=2`，240s 内既无异常也无重试记录
> —— 请求一直在飞。**本票要加的就是这条预算**：语句级 + 连接级超时、LLM 侧瞬态重试
> （挂起属于瞬态，重试即可恢复）。
> 护栏侧 0 次命中（模型没写过非只读语句），但守着它的理由不变：那是 prompt 里一句话的事。
> 跑批器已有一层同源护栏（`app/eval/guard.py`：白名单 + LIMIT 包裹 + 只读事务），本票做到
> 节点层时可参考/抽取。

另外 C01 修掉了「执行失败静默、前端永远转圈」—— 本票在它基础上继续。

---

## 一、只读白名单（执行前的一道闸）

在 `_7_validate_sql` 与 `_9_execute_sql` 之前加一道**语句级**检查（不是正则碰运气，是按 MySQL 语法判定）：

- **必须**以 `SELECT` / `WITH` 开头（`WITH ... SELECT` 是合法查询）
- **禁止词表**：`INSERT` / `UPDATE` / `DELETE` / `DROP` / `TRUNCATE` / `ALTER` / `CREATE` / `GRANT` / `REVOKE` / `LOAD` / `OUTFILE` / `INTO OUTFILE` / `INTO DUMPFILE` / 多语句分隔符 `;`
- 拒绝时**回填可操作错误**给校正节点（#2 的原则：错误信息要能照着做）—— 这样 LLM 有机会自己改对，而不是整图失败

## 二、强制 LIMIT

- 没有 `LIMIT` 的 `SELECT` → **自动补一个**（可配，默认 200），并在日志/报告里记一笔「补过」
- 有 `LIMIT` 但大于上限 → 收紧到上限
- **不要**用「拒绝」处理 —— 缺 LIMIT 是常见且无害的，拒绝会让可执行率虚低

## 三、超时（两层）

- **语句级**：MySQL 侧 `SET SESSION max_execution_time`（毫秒，只作用于 `SELECT`）
- **连接级**：SQLAlchemy 的 `connect_args` 里设 `read_timeout` / `write_timeout` 作为兜底

## 四、重试（只给瞬态）

- **LLM 调用**：502 / 连接错误 / 超时 → 指数退避重试（**只重瞬态**；4xx 直接放弃）—— 与 `CharAgent/retry/policy.py` 的判据同源，可直接借那套思路
- **数据库**：连接断开可重试一次；**语句错误不重试**（那是 SQL 写错了，重试只会再错一次 —— 它该走校正节点）
- 重试**不能吞取消**（若将来接了取消）

---

## 验收

- [x] `DROP TABLE` / `TRUNCATE` / `UPDATE` / `DELETE` / 多语句 → **被拒**，且拒绝理由是给模型看的可操作文本
      （单测 14 条拒绝用例 + **端到端演示**：给 `generate_sql` 塞一条 `DROP TABLE fact_order`，
      护栏拦下且未下发数据库，校正式真模型拿着理由改对成 SELECT，最后执行出 41099.5）
- [x] `WITH ... SELECT` 不被误杀（正面用例；`created_at` / `'drop table'` 字面量 / `/* 注释 */` 都补了不误杀的用例）
- [x] 无 `LIMIT` 的查询被自动补上（默认 200）；超过上限的被收紧（`LIMIT 5000` → 1000）
- [x] 一条故意写慢的查询不会挂住请求：MySQL 侧 `max_execution_time` 实测 3024 掐断，且**连接仍可用**
      （集成用例跑过生产 `dw_client` 本人；另配 `read_timeout` / `connect_timeout` 兜底）
- [x] LLM 返回 502 时整图不再直接失败：假上游（httpx.MockTransport）502×2 → 第三次成功；
      挂起（本地静默服务端）在预算内以 `APITimeoutError` 结束且重试照做
- [x] 语句错误**不**被重试（`connection_invalidated=False` 的 DBAPIError 只下发一次、不回滚）
- [x] C15 的跑分不受本次加固的负面影响（39 题 × 3 次全量重跑对比见实施记录：可执行率 100% 不变，表必命中 / 指标命中 / 召回@merge / 失败率完全不变）

## 开工前要定的 —— 已定（2026-10-06）

| # | 决策 | 结论 |
|---|---|---|
| 1 | LIMIT 默认值与上限 | **200 / 1000**（票面建议值）。用 `SqlGuardLimits` 承载（冻结数据类，构造仓储可换档），不引入 yaml 配置节 —— 这套档位的读者只有仓储构造点一个 |
| 2 | 白名单策略 | **两条都做**：词表拒绝（挡藏在子句里的写操作）+ 只允许 SELECT/WITH 开头（挡多语句），且判关键词前先屏蔽字符串字面量与注释（否则 `'drop table'` 会被误杀） |

**两处按实际能力修正票面假设**（票面写于 2026-09-30，未读码）：

- **asyncmy 没有 `write_timeout`** —— 只有 `read_timeout` / `connect_timeout`（传 `write_timeout` 直接 `TypeError`，实测）。
  连接级这层按驱动实际能力落：`read_timeout=30s` + `connect_timeout=5s`。
- **LLM 重试不自己写退避** —— 交给 OpenAI SDK 的 `max_retries`：它的判据与票面「只重瞬态」一致
  （429 / 5xx / 连接失败 / 超时重试，4xx 直接放弃），自己再包一层只会双重退避、还可能与 SDK 的重试叠加。
  用假上游把「502 会重试」「400 不重试」「挂起在预算内结束」三件事钉成了用例。

**落地位置与票面的差异（写清楚，免得被读成漏做）**：票面说「在 `_7_validate_sql` 与
`_9_execute_sql` 之前加一道检查」。实现落在**执行咽喉** —— `DwMysqlRepository.validate_sql` /
`execute_sql`（两个节点都调它们）：一处实现覆盖两个节点，跑批器复用同一份，不重复造。
C15 在跑批侧写过的 `app/eval/guard.py`（`GuardedDwRepository` 子类）因此**删掉**了：
判据抽成 `app/core/sql_guard.py`，仓储升级成生产实现；旧文件移到 Temp 留档。

## 实施记录

**四层落地**（一处判据 + 三处接线）：

| 层 | 文件 | 做了什么 |
|---|---|---|
| 判据 | `app/core/sql_guard.py`（新） | 白名单（词表 + 开头判定 + 多语句）、LIMIT 补齐/收紧、`GuardNote` 留痕；纯函数，零依赖 |
| 执行咽喉 | `app/repositories/mysql/dw.py` | `validate_sql` / `execute_sql` 过闸；被拒不下发数据库、理由透传给校正节点；只在**连接失效**时重试一次（`DBAPIError.connection_invalidated`），语句错不重试 |
| 数据库侧 | `app/clients/mysql.py` | dw 连接档案：连接建立时 `SET SESSION TRANSACTION READ ONLY` + `max_execution_time`（10s，可配），`connect_args` 给 `read_timeout` / `connect_timeout`；钉不上就不让连接建立（fail closed） |
| LLM 预算 | `app/agent/llm.py` | 显式 `timeout=60s` + `max_retries=2`（此前是 SDK 默认读超时 600s = 没有预算） |

**实测数据**（本机 MySQL 8.0.46）：

| 验的是什么 | 怎么验的 | 结果 |
|---|---|---|
| 只读事务拦写 | 生产 `dw_client` 上 `CREATE TEMPORARY TABLE` | 1792 `Cannot execute statement in a READ ONLY transaction` |
| 锁定读也拦 | `SELECT ... FOR UPDATE` | 同样 1792（白名单之外的第二个覆盖点） |
| 语句预算 | 重笛卡尔积 + `/*+ MAX_EXECUTION_TIME(200) */` | 3024 掐断，耗时 < 0.3s；**之后同一连接照常查询** |
| 会话变量真的钉上了 | `SELECT @@transaction_read_only, @@max_execution_time` | `(1, 10000)` |
| LLM 502 | 假上游 502×2 → 200 | 第三次成功，`attempts == 3` |
| LLM 400 | 假上游 400 | 只打一次，`BadRequestError` 直接抛 |
| LLM 挂起 | 本地静默服务端 + `timeout=0.3` | `APITimeoutError`，用时 1.09s，连接数 2（重试照做） |
| 拒绝→校正闭环 | `generate_sql` 第一次返回 `DROP TABLE fact_order`，校正走真模型 | 护栏拦下（未下发数据库）→ 校正节点按理由改出正确 SELECT → `[{'GMV': 41099.5}]` |
| 建索引不受只读影响 | `python -m app.scripts.build_meta`（它只**读** dw 采字段类型与取值） | 正常完成：5 表 / 24 列 / 98 列向量 / 75 取值 / 2 指标 |
| 端到端链路 | `python -m app.agent.graph`（真模型） | 「统计华北地区的销售总额」→ 补 LIMIT 200 → `[{'sales_total': 41099.5}]`（与基线一致） |

**TDD 抓到的两个真 bug**（都补了回归用例）：

1. **块注释从未被屏蔽**：`sql_guard` 的掩码正则里 `/* ... */` 那一段**漏了开头的 `|`**，
   于是 `SELECT /* 说明 */ 1` 会因注释里出现禁词而被误拒 —— 这个洞从 C15 就在，选单题复跑时才撞出来
2. **`sql_guard` 的 LIMIT 算式**与 `wrap_limit` 的签名重构中发现「掩码用于重写」的老坑仍在文档里，
   顺手把 `strip_trailing_semicolon` 抽成共用函数（扫描与重写两条路共用一份）

**评测报告侧的连带修复**：`--cases` 选到「没有指标题」的子集时，报告渲染层对
`metric_hit_rate is None` 直接崩（`TypeError: 'NoneType' object is not subscriptable`，
从 C15 潜伏至今）—— 改成 `summary_row()` 统一容错，补渲染回归用例。

**跑分对比（验收第 7 条）**：加固后全量重跑 39 题 × 3 次（2026-10-06 01:23 起跑，36 分钟），
落盘 `app/eval/reports/after-c16.{json,md}`，与 `baseline.json` 逐题 diff：

| 指标 | 加固前 | 加固后 |
|---|---|---|
| 列召回率 | 90.5% | 90.3% |
| 列精确率 | 97.4% | 96.8% |
| 表必命中率 / 指标命中率 / 召回@merge | 82.1% / 100% / 94.5% | **完全不变** |
| **可执行率（EX）** | 100% | **100%**（117/117） |
| 运行失败率 | 0% | **0%** |
| 护栏拒绝 / 补 LIMIT | — | 0 次拒绝；117 次全部「无 LIMIT → 已补 200」 |

三处逐题差异**都在过滤阶段的候选集上**（C16 未触碰这条链路）：time-02 有 1 次把「订单数量」
理解成 `SUM(order_quantity)`（加固前 3 次都取 `order_id`）、nom-05 一次多留了 `customer_id`。
同类波动在**加固前那一批的臂内**同样出现过（nom-05 基线 run3 与 run1/2 候选集不同）——
是 LLM 侧的固有抖动，不是加固引入的。护栏本身 117 次全是「包裹」，没有一次拒绝。

**测试**：195 用例全过（新增 `test_sql_guard.py` / `test_dw_repository.py` /
`test_llm_resilience.py` / `test_mysql_hardening.py`，删 `test_eval_guard.py` 随
`app/eval/guard.py` 一起移走）；`ruff check` + `ruff format --check` clean。

**代码评审（2026-10-06，两轴并行：Standards + Spec）抓出并已修的**：

| 抓到的问题 | 怎么修的 |
|---|---|
| **`/*! … */` 是可执行注释**（MySQL 会执行里面的内容），**`--x` 也不是注释**（MySQL 要求 `--` 后跟空白）—— 判据把它们当惰性注释屏蔽，`SELECT 1 /*!80000 ; DROP TABLE t */` 与 `SELECT 1 --x; DROP TABLE t` 都能绕过白名单（评审实测） | 掩码排除 `/*!` / `/*+`；`--` 只在后跟空白或行尾时才算注释。两条绕过各补拒绝用例，真库实测已堵 |
| **外层派生表包裹会把合法 SQL 弄坏**：两表同名列（`SELECT f.date_id, d.date_id … JOIN …`）包进去报 1060 `Duplicate column name`（实测）—— 包裹本意只是加上限，却把「能跑」变成「语法错」 | 上限改成**文本层面**补/收：没 LIMIT 就在末尾另起一行补默认档；超上限就把**最后一个** LIMIT 的数字收到上限；都没超就原样放行。顺带解决「行尾注释吞掉 LIMIT」「字面量被掩码抹掉」两个老坑（`enforce_limit` + 回归用例） |
| **`REPLACE` 被列进禁词表** —— 它同时是只读字符串函数，`SELECT REPLACE(region_name, '省', '')` 被误杀（实测） | 从禁词表移除（写形态 `REPLACE INTO` 由「必须以 SELECT / WITH 开头」挡住）；补正面用例 |
| 多 LIMIT 时取第一个匹配：`(… LIMIT 5) … LIMIT 900` 被包成 5 —— **静默把结果截得比作者要的还小**（不是少几行，是错答案）；`LIMIT 0` 被当成「没写」 | 新机制下两个都消失：只在超上限时动最后一处，`own_limits` 三态（无 / 单个 / 多个）分别处理，`LIMIT 0` 原样放行（各补用例） |
| EX 判定的护栏留痕取 `notes[-1]`，而 EEX 题最后跑的是 **gold SQL** —— 记错对象 | 留痕挪到 `_evaluate_sql` 跑完**被测那条 SQL** 之后取；图内失败的题退回图内最后一次留痕 |
| 「4xx 直接放弃」不精确：SDK 还会重 408 / 409 | 注释与文档改准 |
| README §3.3 声称「`FOR UPDATE` 也会被 1792 拦下」但无用例 | 集成用例补上（真库实跑） |
| 命名半迁移：报告与日志里仍写「跑批护栏」，与「护栏即生产执行咽喉」矛盾 | 统一改「执行护栏」；`limit_wrapped` 键名也改准为 `limit_enforced`（两份落盘报告同步改名，逐 run 数据一个没动） |
| 常量与默认值重复：CLI 里的 200 / 1000、`ReadOnlyProfile` 的默认值与 `DwGuardConfig` 各写一份 | CLI 引用 `DEFAULT_LIMIT` / `LIMIT_CAP`；`ReadOnlyProfile` 字段改必填（默认值只留 `DwGuardConfig` 一处） |
| 注释标点：新增文件里混进全角 `。`/`,`（仓库标准要求注释用英文标点，既有文件 0 处） | 45 行统一改回 ASCII；**面向模型/用户的文案保持中文标点**（那不属于注释规范） |
| 测试偏软：`raises(Exception)` + 字符串查错误码；配置替身缺 float 分支导致预算断言空转；`describe` 四个分支挤一条用例 | 改断 `OperationalError` + `orig.args[0] == 1792 / 3024`；替身补 float 占位；`describe` 拆成参数化；另加一条**不依赖配置**的「参数真的落到 SDK 客户端」机制用例 |

**评审指出但不改的（记在这里，不留沉默）**：

- **只读连接档案超出票面「三、超时（两层）」**：有意的加法 —— 票面自己点出「DDL 隐式提交，
  生成 DROP 就真生效」是最容易被问穿的一条，只靠白名单太薄；dw 库本来就只读（建索引时也只
  读它采元数据），钉只读不影响任何既有链路（`build_meta` 实测照跑）。
- **禁词表比票面多几个**（RENAME / CALL / HANDLER / LOCK）：都是管理类语句，没有只读用法；
  票面那份是「等」字列举，不是穷举。`REPLACE` 已按上面的理由移出。
- **asyncmy 默认开着 `MULTI_STATEMENTS`**（实测 `SELECT 1; SELECT 2` 两条都执行）：没有去
  关驱动的 flag（可能伤到建索引的批处理路径），而是把「只允许单条语句」当**承重判据**
  （写进 README §3.3）；即便这一条漏了，只读事务仍会挡住写的那半。

**一处评审后顺带改的实现、与基线的关系**：LIMIT 判据从「派生表包裹」换成「文本补/收」发生在
`after-c16` 跑批**之后**。它对那份报告没有影响 —— 117 次运行全部 `had_own_limit=False`
（都是无 LIMIT 的查询，新旧机制给出同一个 200 行上限），且 0 次拒绝（关键词表的改动也波及不到）。

## 改了哪些文件

| 文件 | 说明 |
|---|---|
| `app/core/sql_guard.py` | 新增：护栏判据（从 `app/eval/guard.py` 抽出并升级） |
| `app/repositories/mysql/dw.py` | 执行咽喉过闸 + 连接失效重试一次 + 留痕 |
| `app/clients/mysql.py` | 新增 `ReadOnlyProfile`；dw_client 挂只读档案（连接事件 + connect_args） |
| `app/agent/llm.py` | 显式 timeout / max_retries |
| `app/conf/app_config.py` | 新增 `dw_guard` 节（有默认值）+ `llm.timeout_s` / `llm.max_retries` |
| `app/eval/guard.py`、`tests/test_eval_guard.py` | **删除**（判据已抽到 `app/core/`，仓储已是生产实现）；旧文件移到 Temp 留档 |
| `app/eval/runner.py` | 改用生产仓储与生产档位（新增 `--limit-cap`）；去掉 `make_readonly` / 子类 |
| `app/eval/report.py` | 护栏行改述为「生产执行咽喉」；`summary_row()` 容错（None 块不再崩） |
| `tests/test_sql_guard.py` `test_dw_repository.py` `test_llm_resilience.py` `test_mysql_hardening.py` | 新增 4 个用例文件（含 MySQL 集成与假上游） |
| `app/eval/reports/after-c16.json` `after-c16.md` | 新增：加固后全量重跑的对照报告（验收第 7 条的产物，随仓库提交） |
| `tests/doubles.py` | `FakeAsyncSession` 支持脚本回放（断连→成功）与 rollback 计数 |
| `README.md` | 新增 §3.3 执行护栏；§2 目录树 / §3.2 节点表 / §5.2 配置 / §6.2-4 / §8.4 同步 |
| `CLAUDE.md` | §5.6 加「执行护栏」行 |
| `.scratch/Charlotte/PLAN.md` | 进度行：C16 移入已 done |
