# C16 · rag_text2sql 安全与健壮（只读白名单 + LIMIT + 超时 + 重试）

**Status:** todo

**Type:** hardening

**Blocked by:** C15（先有评估，才知道加固有没有把结果改坏）

**上游:** `app/agent/nodes/_7_validate_sql.py` / `_9_execute_sql.py`；`app/repositories/mysql/dw.py`；`.scratch/Charlotte/PLAN.md` §2 组 C

## 现状（2026-09-30 读码，四条都是硬事实）

| 事实 | 后果 |
|---|---|
| **唯一的护栏是 prompt 里的一句话**（`prompts/generate_sql.prompt:20`「只能用于查询」） | LLM 一旦生成 `DROP` / `TRUNCATE` 就**真的执行** |
| **无强制 LIMIT** | 大结果集全拉回应用层 |
| **无超时** | 一条慢查询挂住整个请求 |
| **无重试** | 全仓 grep `timeout|retry|backoff|tenacity` **零命中**；LLM 一次 502 就整图失败（日志里 03:11/03:12/03:16 多次 `Error code: 502` 直接中断） |

**唯一缓解着的一条**（要如实写进文档）：全程**没有任何 `commit()`**，所以普通 DML 会随 session 关闭回滚。**但 MySQL 的 DDL 是隐式提交** —— 生成 `DROP` / `TRUNCATE` 就真生效。**这条差别是面试官最容易问的点，别答错。**

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

- [ ] `DROP TABLE` / `TRUNCATE` / `UPDATE` / `DELETE` / 多语句 → **被拒**，且拒绝理由是给模型看的可操作文本
- [ ] `WITH ... SELECT` 不被误杀（这是最容易被白名单误伤的合法形态，要有正面用例）
- [ ] 无 `LIMIT` 的查询被自动补上；超过上限的被收紧
- [ ] 一条故意写慢的查询不会挂住请求（在超时内结束）
- [ ] LLM 返回 502 时整图不再直接失败（重试后成功，或重试耗尽后给出可读的错误）
- [ ] 语句错误**不**被重试（与业务错误区分开）
- [ ] C15 的跑分不受本次加固的负面影响（跑一遍对比）

## 开工前要定的

- LIMIT 的默认值与上限（建议 200 / 1000）
- 白名单是「按词表拒绝」还是「正则只允许 SELECT/WITH 开头」—— 建议**两条都做**（前者挡藏在子句里的写操作，后者挡多语句）

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
