# 20 · 会话管理动作：重命名 / 删除 / 搜索 / 置顶

**Status:** done

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
| 迁移文件命名是 `<version>_<slug>.py`（`alembic.ini` **刻意没有** `file_template`），首个是 `0001_core.py` | `alembic.ini:26-28`、`versions/0001_core.py` |
| 迁移写法惯例：docstring 头（含 `Revision ID` / `Revises` / `Create Date`）· `revision` / `down_revision` 变量块 · `upgrade()` 与 `downgrade()` **逐条显式**（`downgrade` 顺序与 `upgrade` 相反） | `0001_core.py:1-54`、`:64-68`、`:623-659` |
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

- **删除为什么是软删**：硬删会顺着外键把 `runs` / `messages` / `tool_calls` 一起 CASCADE 掉，而 **L3 的成本记账正挂在 `runs` 表上** —— 硬删会让「上周花了多少钱」失真。用户语义上的"删除"就是"从我的列表里消失"，软删完全满足。**另外别忘了帧**（ticket 24 起 `charagent_checkpoints.thread_id` 也是 CASCADE）—— 硬删还会把这段会话的全部快照一起带走，那正是「模型为什么忘了」要查的东西
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

- [x] 迁移 `0002` 在空 schema 上 `upgrade head` 成功；`downgrade base` 干净移除；`compare_metadata` 零差异（`pytest -m pg_db`，**默认不跑的标记，验收要显式跑**）
- [x] 新列都有 `comment=`；`test_db_schema.py` 的断言与索引清单已同步
- [x] `pinned_at` / `deleted_at` 在两个仓储查询里都生效：删除的会话**不出现在列表**、置顶的**排在最前**
- [x] 重命名：改完列表与 `GET /conversations` 都反映新标题；**改标题不会把会话顶到列表最前**（`updated_at` 不动）
- [x] 删除：软删（`deleted_at` 有值、行还在、`messages` 一行不少）；再删一次不报错；列表里消失
- [x] 搜索：标题命中与正文命中都能找回会话；大小写不敏感；只搜当前 `(tenant, user)` 的；`limit` 生效
- [x] 四个动作都做**归属校验**：别人的会话编号一律 404（不是 403）
- [x] BFF 四个转发都是 POST + CSRF；会话编号**不在** URL 里（有用例）
- [x] 前端：重命名 / 置顶 / 删除三处可用；删除有二次确认；删掉当前会话后切到一个新的空对话
- [x] 前端：搜索能用、有防抖、清空恢复、搜不到有空态
- [x] 置顶后刷新页面仍在最前（后端排序生效，不是前端排序）
- [ ] `pytest CharAgent`（含 `-m pg` / `-m pg_db`）· `pytest CharApp` · `manage.py test app.minimall`（**用户手动跑**）全绿；`ruff` 干净

> 最后一条只差**整仓那一跑**：框架 **1020 passed** · 标记集（`pg`/`pg_db`/`redis`）**73 passed** ·
> 业务 **198 passed** · BFF 那个模块 **100 passed** · `ruff check` / `format --check` 干净；
> 整个 `app.minimall` 那套按惯例留给用户手动跑。

## 备注

- **为什么搜索不单独开一个端点**：它是"列表的一个过滤条件"，做成可选参数后端点数量不涨、前端也只需维护一个数据源。真正的搜索服务（全文索引 / 相关性排序）是另一个量级的事，本项目不做 —— 这条边界写进注释
- **软删的下游影响**：`threads` 行留着、`messages` 留着，所以 L3 的成本统计不会因为一次删除而失真。**代价**：需要有人（或一条定时任务）在很久之后真删掉 —— 本项目不做，写进「已知边界」
- **重命名与「标题取自首条用户消息」的关系**：自动标题只在**创建时**写一次，用户改名之后不会再被覆盖。这条要在用例里钉住（否则将来有人"优化"成每次写入都刷标题，用户的命名就白改了）
- **`pinned_at` 而不是 `pinned` 布尔**：布尔能表达"置不置顶"，表达不了"谁先置顶的"。既然要加列，就加能撑住排序语义的那个

---

## 实际开发情况

2026-09-23 完成。**四层都动了**（这是 L2.5 里唯一一片跨满四层的新功能）：
框架 db 层（两列 + 三个仓储方法）· 框架 HTTP 层（一组会话路由）· Django BFF（四条转发）·
前端（搜索框 + 每项的管理菜单）。

### 一、定案（票里没写死、实现时定的）

| 决定 | 理由 |
|------|------|
| 三个写方法的归属判据收进**一个 keyword-only 必填组** (`_owned(tenant_id, user_id)`) | 它们比读危险：读错了只是看见别人的，写错了是**改**了别人的。归属不进签名默认值 —— 漏了就 TypeError |
| 「没改到」= **404** 而不是另开一条分支 | 归属写在仓储的 WHERE 里，所以「不是你的」天然表现为「一行都没改到」；两条路合成一条，就没有「漏判归属」的位置 |
| 搜索**不另开方法**，是 `list_active_with_messages(..., query=)` 的一个参数 | 票的备注自己就说「搜索是列表的一个过滤条件」；做成参数之后，「所有会话查询都带 `deleted_at IS NULL`」才有唯一一处可守 |
| 列表响应多给一个 `pinned_at` | 前端那颗菜单要显示「置顶」还是「取消置顶」——少这个字段，页面只能猜 |
| BFF 把上游的 `thread_not_found` **原样转 404**（其余 404 塌成 502） | 与取消那条路同一个坑：用户可能只是在另一个标签页里删掉了它，那不是故障。判据必须带上**那个码**（不带码的 404 是路由不在 = 接线故障） |
| 三个端点体抽出 `_conversation_action` | 「归属 → 没改到 → 404」这条规则收成一处（审查提的，见「五」） |
| 前端改完**统一重拉列表** | 排序规则归后端（置顶要跳到最前），页面自己挪 DOM 就是把那份规则再实现一遍 |

### 二、与票有意不一致的四处

1. **搜索没做成独立方法**（票 §2 写的是 `search(...)`）—— 理由见上表。改了一个参数，
   换来的是可见性那条纪律只有一处。
2. **软删没有保留「第一次删除的时刻」**：票只要求「幂等」，实现是直接写当下
   （第二次调用会覆盖第一次的时间戳）。想留住第一次要写 `coalesce(deleted_at, :now)`，
   而那是个**表达式** —— 本仓储的写入形状一律是「等值条件 + 直接给值」（测试替身也只认
   这个形状），为一列排查用的时间戳破掉它不值。这条写进了 ADR-0007 的代价一节。
3. **`list_for_tenant` 也加了 `deleted_at IS NULL`**（票说的是「所有会话查询」）——
   管理端那条查询因此也看不到被删的。这不是疏漏：ADR-0007 里写清楚了「要一个含已删的
   视图得另开一个显式说明意图的方法，本项目不铺」。
4. **BFF 那一侧仍是 POST + 请求体**（上游那一跳才是票里写的 `GET /conversations?q=`）：
   搜索词在浏览器这一侧走请求体（它完全可能是一个订单号），由 BFF 翻成上游的查询串。
   两套 wire 契约各按各的形状。

### 三、多做的两件

1. **LIKE 通配符转义**：搜一个下划线本来会命中**所有**会话（`_` 是「任意一个字符」），
   而那个键很容易被敲进去。`_escape_like` + `ESCAPE '\'`，四条用例钉住。
2. **前后端各卡一道标题长度**：BFF 卡住了用户看到的是「名字太长」；让它穿到上游再被打
   回来，用户看到同一句话但要绕一趟网络。

### 四、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `CharAgent/db/schema.py` · `alembic/versions/0002_thread_management.py`（新） · `db/entities.py` | 两列 + 增量迁移 + 实体 docstring |
| `CharAgent/db/repositories/threads.py` | `_visible()` / `_owned()` / `_visible_messages()` / `_matches()`；列表加过滤与 `query`；三个写方法；排序改成置顶优先 |
| `CharAgent/server/conversations.py` · `app.py` · `utils/errors.py` · `__init__.py` | 四条路由（列表带 `q` + 三个写动作）、`read_query` / `read_title` / `read_pinned`、`_conversation_action`、`ThreadNotFoundError` |
| `app/minimall/views_bff.py` · `urls_bff.py` | 四条转发（抽 `_call_upstream`，五个 URL 建造器收成 `_url`）、三个动作视图（抽 `_AgentConversationActionView`）、四个错误码话术 |
| `templates/minimall/agent.html` | 搜索框 + 300ms 防抖；每项一颗「⋯」菜单；就地改名；`confirm` 二次确认；删当前会话换新的空对话 |
| 测试 | `test_db_store.py` +7 · `test_server_conversations.py` +13 · `test_bff.py` +26 · `test_db_alembic.py`（版本号从 0001 推到 0002、`history` 两条）· `doubles.py`（拆平 `and_`） |
| 文档 | `db/README.md`（迁移表两条）· `CharApp/CONTEXT.md`（置顶 / 删除两个词条）· `PLAN.md` §6.3 · `docs/adr/0007-*.md`（新）· 三个说「多一条路由」的旧说法 |

### 五、真机验到哪一步

**迁移先在真库上跑通**（那是「第一条增量迁移」的真正考验 —— 空库跑一次不算）：

| 检查 | 读数 |
|------|------|
| `alembic upgrade head`（库里已有 0001 的表与数据） | `Running upgrade 0001_core -> 0002_thread_management` |
| `charagent_threads` 新列 | `pinned_at` / `deleted_at`，都是 `timestamp with time zone` + 可空 |
| 版本表的 `history` | 两笔：`0001_core: {from: null}` · `0002_thread_management: {from: "0001_core"}` |
| `name` | 「会话管理两列: 置顶时刻与删除时刻」（取 docstring 首段） |

**再看浏览器**（Playwright + 真 DeepSeek + 真 Postgres；起服务时故意用 `python -m
CharAgent.minimall.server` 之外的方式另开了一对端口，避开上一片留下的旧进程）：

| 核的哪一条 | 现场读数 |
|---|---|
| 建两段会话 | 左栏两条，新的在前 |
| 改名 | 「我最近的订单到哪了」→「订单查询那一段」，回车后列表刷新、标题变了、**顺序没变**（`updated_at` 确实没动） |
| 置顶 | 第二项跳到最前；菜单措辞变成「取消置顶」；**刷新之后仍在最前**（后端排序，不是前端挪的）；取消置顶后回到按活动排序 |
| 搜索 | 搜「订单」→ 一条；搜「没有这个词」→ 空态「没搜到相关的对话」；清空 → 全量。**每敲一段只发一个请求**（防抖生效，抓了请求流水核对） |
| 删除 | 弹窗「删除这段对话? 删除后它就不再出现在列表里了.」→ 确认后从列表消失 |
| 删掉**当前**那一段 | 会话标签换成新编号、主区清空、「试试这样问」回来 |
| 软删的证据 | 库里两行都还在、`deleted_at` 有值，而**10 条消息一条不少** |

**真机上抓到的两个 bug**（都是这一片新写的代码，都被现场抓到并修掉）：

1. **「点了重命名没反应」** —— `document` 上那个 click 处理器（收菜单用）会在「重命名」
   按钮的事件冒到 document 时，把**刚刚建出来的** input 当场取消掉。改成 `closeMenus`
   只管菜单，「点别处放弃改名」交给 input 自己的 `blur`。
2. **「搜了订单，列表过一秒又变回全部」** —— 列表会被四个地方拉（首屏 / 每轮答完 /
   搜索防抖 / 管理动作之后），**两次请求重叠时慢的那份会盖掉快的那份**。加了一个
   自增 ticket：回来的时候不是最新那次就丢掉。

跑完把探针数据清干净了（四张表回 0）。

### 六、代码审查改了什么（两轴，各一个 sub-agent）

**Standards 轴**

| 发现 | 处置 |
|------|------|
| 硬违规：`doubles.py` 的 `_flatten_and` docstring 以全角句号收尾（`CLAUDE.md` §4.9） | 改成 `.` |
| 硬违规（**真错**）：`threads.py` 的模块 docstring 被我改成「**每一个**方法都强制带 tenant_id」—— 而 `get` / `touch` / `set_title` 都不带，等于把一句准确的话改成假话 | 改回准确的说法，并补一句说明「按主键单个取的不带、三个写方法都带」 |
| 硬违规：三个端点的响应键写字面量 `"conversation_id"`，而旁边就 import 着 `CONVERSATION_ID_FIELD` | 用常量 |
| 文档与代码不一致：`conversations.py` 的字段表里 `updated_at` 那行还写「按它倒序」，没反映置顶排序 | 补上「置顶的除外」 |
| 判断题：三个端点体逐字相似（而 BFF 那侧同形状已经抽了基类） | 抽出 `_conversation_action`（认证 → 落库 → 没改到就 404 → 回显） |
| 判断题：`_matches` 与 `_has_visible_message` 各写一遍同形状的 EXISTS 条件 | 抽出 `_visible_messages()` |
| 判断题：`deleted_conversation_from_request` 这个名字读起来像「已删除的会话」 | 改名 `conversation_to_delete_from_request`，并说明为什么不为它造 dataclass |
| 判断题：`input.maxLength = TITLE_CLIP * 5` 拿**展示**截断常量算**校验**上限 | 页面自己的 `MAX_TITLE_LENGTH = 100` |
| 未采纳：五个 `forward_*` 是 `_call_upstream` 的一层转发（Middle Man） | **不改** —— 它们是具名接缝，文档与测试都按那几个名字指路；内联回去只会让调用点变成一长串参数 |

**Spec 轴**

| 发现 | 处置 |
|------|------|
| 悬空引用：0002 的 docstring 写「见 `db/README.md` 的已知边界」，而那个文件**没有**这一节 | 改指 ADR-0007（真删那条边界确实记在那里） |
| **与票相抵触**：`delete_conversation` 的 docstring 写「仓储那条 `coalesce` 的语义」，而实现早就改成直接赋值了（第二条里说的第 2 点） | 改文档 |
| ADR-0007 写「管理端 / 排查仍查得到那几行」，而 `list_for_tenant` 加了过滤 —— **自己和自己打架** | 改 ADR：按编号 `get` 得到，但列表查询一律不返回 |
| 票里点名「删了之后历史仍读得到 —— 这条要想清楚并**写进用例**」 | pg_db 那条软删用例的 docstring 点名这句，并说明它的两半各由哪条断言兑现 |
| 「`/history` 的端到端用例」没加 | **有意的**：那条路由**一行都没动**、也不含任何 `deleted_at` 过滤，而测试替身本来就不做过滤 —— 写出来的会是一条永远为真的空用例。真正的证据是「消息一行不少 + 按编号仍取得到行」，那两条在 pg_db 用例里 |

**复跑**：框架 **1020 passed** · 标记集 **73 passed** · 业务 **198 passed** ·
BFF 模块 **100 passed** · `ruff check` / `format --check` 干净 · 页面 JS 过 `node --check`。

### 七、留给后面的

- **`app.minimall` 全量那一跑**由用户手动跑
- **服务进程**：这一片又起了几个（CharApp :1007/:1008 + Django :8003 / :8004 / :8005 /
  :8006），`kill` 被权限拦下 —— 与上一片留下的那些一起，并进 issue 21 的「现场清理」
- **已知边界**（ADR-0007 里也写了）：软删的数据只会变多，真删要有人或一条定时任务在
  很久之后做，本项目不做；用户删完之后想找回来说不了话（没有回收站 / 撤销）
