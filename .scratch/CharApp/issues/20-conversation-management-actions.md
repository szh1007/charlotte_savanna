# 20 · 会话管理动作：重命名 / 删除 / 搜索 / 置顶

**Status:** ready-for-agent

**Type:** task

**Blocked by:** 19

**上游:** `../PRD.md` §4.9（L2.5 行）、`CharApp/docs/PLAN.md` §5（L2.5）

## 做什么

给会话列表补四个管理动作 —— **重命名 · 删除 · 搜索 · 置顶**（用户 2026-09-22 追加要求）。这四件都是**新的存储能力**：`charagent_threads` 表里没有能表达它们的列，`ThreadsRepository` 也**一个 update / delete 方法都没有**。

顺带把框架的 **`0001` 之后的第一个增量迁移**做出来 —— 迁移机制从此不再只是「初始那一次」。

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| `ThreadsRepository` 只有 `add` / `get` / `list_for_tenant` —— **没有任何 update / delete** | `CharAgent/db/repositories/threads.py:34`、`:84`、`:89` |
| 写库用**手写 `_params` 字典**（键就是列名）—— 加列必须同步它 | `threads.py:119-130` |
| `update(...).where(...).values(...)` + `bool(rowcount)` 的现成写法在 `RunsRepository.try_transition` / `set_status` | `db/repositories/runs.py:145-154`、`:172-182` |
| `threads` 表列定义的**唯一来源** | `db/schema.py:82-132` |
| `list_for_tenant` 现在的排序是 `updated_at DESC, thread_id DESC` | `threads.py:108-113` |
| 加一列要动 **6 处**：`schema.py` 表定义 · 新 revision 文件 · `Thread` 实体 · 仓储 `_params` · `tests/test_db_schema.py` 的硬编码断言 · `db/README.md` 的「改 schema 的流程」 | `db/schema.py:82` · `alembic/versions/` · `db/entities.py:133-157` · `threads.py:119-130` · `tests/test_db_schema.py` · `db/README.md:68-79` |
| 迁移文件命名是 `<version>_<slug>.py`（`alembic.ini` **刻意没有** `file_template`），首个是 `0001_charagent_core.py` | `alembic.ini:26-28`、`versions/0001_charagent_core.py` |
| 迁移写法惯例：docstring 头（含 `Revision ID` / `Revises` / `Create Date`）· `revision` / `down_revision` 变量块 · `upgrade()` 与 `downgrade()` **逐条显式**（`downgrade` 顺序与 `upgrade` 相反） | `0001_charagent_core.py:1-30`、`:40-43`、`:493-512` |
| 可空时间列的既有范例：`runs.finished_at`（`nullable=True` + comment 说清 NULL 什么含义） | `db/schema.py:213-216` |
| `test_db_schema.py::test_every_column_has_a_comment` **强制**新列写 `comment=` | `tests/test_db_schema.py:53-65` |
| `test_db_alembic.py` 会**逐列比**迁移产物与代码定义（列名 / 类型 / 可空 / 主键）并跑 `compare_metadata` 断言零差异 —— 改了 schema 忘改迁移会当场红 | `tests/test_db_alembic.py:122-169`、`:205-234` |
| `test_expected_indexes_exist` 是硬编码的索引清单 | `tests/test_db_schema.py:134-150` |
| `messages` 表有 `content`（Text，可空）与 `(thread_id, created_at)` 索引 | `db/schema.py:226-288` |
| `MessagesRepository._list` 的限流做法：先按倒序取 `limit` 条再 `reversed` | `db/repositories/messages.py:238-243` |
| 会话编号 = `thread_id` 的第三段（`minimall:{user_id}:{conversation_id}`）；`thread_id` 有 128 字符上限校验 | `CharApp/minimall/service.py:157-164`、`CharAgent/checkpoint/utils/types.py:54-88` |
| **既有纪律：浏览器的三条请求都是 POST + 请求体**（会话编号不进 URL —— 访问日志 / 浏览器历史 / Referer 都会跟着 URL 走） | ADR-0002、issue 13 的「用户复核时提的一条」 |
| 框架侧的**下游那一跳**是 GET + `X-Conversation-Id` 头（同机同信任域，地址里不带参数） | issue 13 的同一条复核 |

## 具体任务

### 1. 迁移 `0002`（两列）

| 列 | 形状 | 含义 |
|----|------|------|
| `pinned_at` | `DateTime(timezone=True), nullable=True` | NULL = 未置顶；有值 = 置顶时刻（**时间戳而不是布尔**：它能表达"最近置顶的排前面"，且与 `updated_at` 同型、排序表达式对称） |
| `deleted_at` | `DateTime(timezone=True), nullable=True` | NULL = 还在；有值 = 已被用户删除（**软删**） |

- **删除为什么是软删**：硬删会顺着外键把 `runs` / `messages` / `tool_calls` 一起 CASCADE 掉，而 **L3 的成本记账正挂在 `runs` 表上** —— 硬删会让「上周花了多少钱」失真。用户语义上的"删除"就是"从我的列表里消失"，软删完全满足
- 文件按惯例命名（`0002_thread_management.py`），6 处一起改；`test_db_schema.py` 的列注释与索引断言同步

### 2. 仓储方法（`ThreadsRepository`）

| 方法 | 语义要点 |
|------|---------|
| `update_title(thread_id, title)` | 只改 `title` —— **不动 `updated_at`**（改标题不算"活动"，否则改个标题就把会话顶到列表最前） |
| `set_pinned(thread_id, pinned: bool)` | 置顶写 `now()`，取消置顶写 NULL |
| `soft_delete(thread_id)` | 写 `deleted_at = now()`；**幂等**（再删一次不报错） |
| 列表与搜索 | **所有**会话查询都要加 `deleted_at IS NULL`，这一条封在一个地方，别散在各处 |

- 排序改成 **`pinned_at DESC NULLS LAST, updated_at DESC, thread_id DESC`**（置顶项永远在最前；`NULLS LAST` 是 PG 的写法，注意 SQLAlchemy 的语法）
- 搜索给一个 `search(tenant_id, user_id, query, *, limit)`：**标题 + 消息正文都搜**（大小写不敏感），命中任一处就返回该会话
  - 用 `ILIKE`（PG 的 `pg_trgm` / 全文索引**不引** —— 本项目会话量级下顺序扫够用，这条写进注释，理由与"以后再说"都写清）

### 3. 端点形状（**倾向如下，实现时可微调但别破坏既有纪律**）

| 动作 | 形状 | 理由 |
|------|------|------|
| 列表 + 搜索 | `GET /conversations?q=<可选>&limit=<可选>` | **搜索做成列表端点的可选参数**，零新端点；读操作本来就是 GET |
| 重命名 | `POST /conversations/title`，头 `X-Conversation-Id`，体 `{"title": "..."}` | 会话编号**不进 URL**（与 `/history` 同一条纪律） |
| 删除 | `POST /conversations/delete`，头 `X-Conversation-Id` | 同上 |
| 置顶 / 取消置顶 | `POST /conversations/pin`，头 `X-Conversation-Id`，体 `{"pinned": true\|false}` | 同上 |

- 四个动作都要**校验归属**：会话必须属于当前 `(tenant_id, user_id)`，否则 404（不是 403 —— 不要泄漏"这个编号存在"）
- 标题长度上限（比如 100 字）+ 空标题的处理（**建议拒绝** —— 空标题会让列表出现空白行）
- 删除后 `GET /conversations` 不再返回它；但**历史仍读得到**（软删不改记录内容）—— 这条要想清楚并写进用例：删了之后还能不能点进去？**答：不能**（列表里没了），但服务端数据还在

### 4. BFF 转发（`app/minimall/views_bff.py`）

- 四个动作都照 `AgentHistoryView` 的形状：**浏览器面 POST + 请求体 + CSRF**，会话编号走请求体、由 `_service_headers` 那三个头带下去，下游那一跳是 GET/POST + `X-Conversation-Id` 头
- 返回体原样透传（BFF 不解析上游 JSON）
- 上游错误沿用 `ERROR_COPY` 话术表

### 5. 前端

- 每个会话项加一个「⋯」（或悬停出两个图标）：**重命名 / 置顶（取消置顶）/ 删除**
- 重命名：就地输入（**不要用 `contenteditable`** —— 与 `textContent` 的既有纪律冲突；用一个小 input 替换标题行）
- 删除：**要二次确认**（不可逆的用户预期）+ 若删的是当前会话，切到一个新的空对话
- 置顶：置顶项移到列表最前（后端排序已保证，前端重拉列表即可）
- 搜索：左栏顶部一个输入框，输入即搜（**防抖**，比如 300ms），清空恢复全量列表；搜不到时显示一句空态

## 验收

- [ ] 迁移 `0002` 在空 schema 上 `upgrade head` 成功；`downgrade base` 干净移除；`compare_metadata` 零差异（`pytest -m pg_db`，**默认不跑的标记，验收要显式跑**）
- [ ] 新列都有 `comment=`；`test_db_schema.py` 的断言与索引清单已同步
- [ ] `pinned_at` / `deleted_at` 在两个仓储查询里都生效：删除的会话**不出现在列表**、置顶的**排在最前**
- [ ] 重命名：改完列表与 `GET /conversations` 都反映新标题；**改标题不会把会话顶到列表最前**（`updated_at` 不动）
- [ ] 删除：软删（`deleted_at` 有值、行还在、`messages` 一行不少）；再删一次不报错；列表里消失
- [ ] 搜索：标题命中与正文命中都能找回会话；大小写不敏感；只搜当前 `(tenant, user)` 的；`limit` 生效
- [ ] 四个动作都做**归属校验**：别人的会话编号一律 404（不是 403）
- [ ] BFF 四个转发都是 POST + CSRF；会话编号**不在** URL 里（有用例）
- [ ] 前端：重命名 / 置顶 / 删除三处可用；删除有二次确认；删掉当前会话后切到一个新的空对话
- [ ] 前端：搜索能用、有防抖、清空恢复、搜不到有空态
- [ ] 置顶后刷新页面仍在最前（后端排序生效，不是前端排序）
- [ ] `pytest CharAgent`（含 `-m pg` / `-m pg_db`）· `pytest CharApp` · `manage.py test app.minimall`（**用户手动跑**）全绿；`ruff` 干净

## 备注

- **为什么搜索不单独开一个端点**：它是"列表的一个过滤条件"，做成可选参数后端点数量不涨、前端也只需维护一个数据源。真正的搜索服务（全文索引 / 相关性排序）是另一个量级的事，本项目不做 —— 这条边界写进注释
- **软删的下游影响**：`threads` 行留着、`messages` 留着，所以 L3 的成本统计不会因为一次删除而失真。**代价**：需要有人（或一条定时任务）在很久之后真删掉 —— 本项目不做，写进「已知边界」
- **重命名与「标题取自首条用户消息」的关系**：自动标题只在**创建时**写一次，用户改名之后不会再被覆盖。这条要在用例里钉住（否则将来有人"优化"成每次写入都刷标题，用户的命名就白改了）
- **`pinned_at` 而不是 `pinned` 布尔**：布尔能表达"置不置顶"，表达不了"谁先置顶的"。既然要加列，就加能撑住排序语义的那个
