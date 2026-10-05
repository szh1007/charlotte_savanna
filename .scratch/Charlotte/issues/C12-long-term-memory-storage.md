# C12 · 长期记忆-a：记忆模型与存储（情景 + 语义两层）

**Status:** done

**Type:** feature

**Blocked by:** C05（新代码走统一日志出口）

**上游:** `CharAgent/docs/DESIGN.md` §4 ④ 的 #31/#32/#33（全册 P2）；`CharAgent/db/README.md:142-149`（**`memories` 表早已规划、未实现**）；`.scratch/Charlotte/PLAN.md` §6.3 的 MEM-D1 / MEM-D2 / MEM-D3

## 现状（2026-09-30 读码）

- **`memories` 表在 `db/README.md:142-149` 的「追加路径（结构已预留）」里已经规划**（P2，未实现），同批的 `idempotency_keys` 已于 2026-09-25 落地。落地方式是「自增下一号迁移」。
- **`charagent_messages` 的 `hidden=True` 已经是「压缩摘要 / 工具回填 / 续写指令」的存放地**（`db/schema.py:364-371`）。
- `CharAgent/hooks/utils/types.py:29-48` 的 `HookPoint` 共 6 个：观察类（`AFTER_TURN` / `ON_TOOL_EXECUTED` / `ON_EVENT`）适合**落库**（插件抛异常只记一笔、不拖垮用户任务）；`BEFORE_TURN` 的 `messages` 是**账本本体（活引用）**，注入进去的内容**会被持久化进 `state.messages` 与每一帧快照**。

**最后那条决定了本票的一个关键取舍**：**不用注入**。你 Q12 定的是「模型主动调 `recall` 工具」—— 走工具、不走注入，账本污染的问题自动绕开。

---

## 一、表设计（`CharAgent/db/schema.py` 新增）

| 列 | 说明 |
|---|---|
| `memory_id` | PK |
| `tenant_id` / `user_id` | **NOT NULL** —— #32 的硬性安全要求，检索强制过滤，**不靠「相信模型不乱看」** |
| `kind` | 两层：`episodic`（情景：带时间戳的历史事件）/ `semantic`（语义：偏好与事实） |
| `content` | 一句话事实（**提炼后的**，不是原文） |
| `source_thread_id` / `source_run_id` | 可追溯「这条记忆是哪次对话里来的」 |
| `created_at` / `updated_at` / `last_used_at` | `last_used_at` 给「用得多的排前面」留口 |
| `deleted_at` | **软删** —— 对齐 #31「废弃用软删标记保留可追溯性」，也与你会话软删的既有约定同源 |

按 `db/` 的既有约定：表名 `charagent_` 前缀、**每列必须写 `comment=`**（有用例强制）、手工写 alembic 迁移（下一号，`db/README.md:88-99` 记着现有四条）。

## 二、为什么存「提炼后的事实」而不是「每轮摘要」（MEM-D2）

`charagent_messages` 里 `hidden=True` 的行**已经**是压缩摘要的家。再存一份"每轮摘要"就是双写 —— 而双写意味着**两份会漂**、查询时要判断信哪一份。

所以记忆存的是**跨会话仍然有用的一句话**：「这个买家偏好货到付款」「他上次说收货地址在公司」。它与 transcript（给人看）、与摘要（给模型看的上下文替换物）**是三件不同的东西**。

## 三、时间衰减与容量淘汰（#33）

- **排序分** = `权重 × 衰减(now − created_at)`，衰减函数与半衰期**可配**
- **容量淘汰**：每个 `(tenant, user)` 最多 N 条，超了淘汰**衰减后分值最低**的（先软删，不物理删）
- **⚠️ 参数是拍的，要如实说**：半衰期与 N 没有真实使用数据支撑。**参数全部可配 + 在模块 docstring 里写明「这是初始值，等有真实使用数据再调」** —— 这比假装它们是调优出来的要好，也是面试里「我知道哪些数是拍的」的证据

## 四、仓储与落地

- 新增 `db/repositories/memories.py`（对齐 `threads.py` / `message.py` 的既有写法）
- **读写都强制带 `(tenant_id, user_id)`** —— 仓储的方法签名里就要有这两个参数，不给「只按 memory_id 查」的口子
- `db/README.md` 的「追加路径」表里把 `memories` 从 P2 挪到已实现，并记一条迁移
- 框架根门面（`CharAgent/__init__.py`）导出新的类型与仓储 —— 注意它有「防漂移用例」（`tests/test_root_facade.py`），加了导出要同步

---

## 验收

- [x] 表建起来，每列有 `comment`，`test_db_schema.py` 的强制用例通过
      —— 七张表的注释/前缀/主键/索引断全绿；`test_db_alembic.py` 的零差异门也过
- [x] 仓储的每个读方法都带 `(tenant_id, user_id)`；**换个用户查不到别人的记忆**（否定断言）
      —— `test_memories_are_scoped_to_the_owner`（换用户 / 换租户两个方向）
- [x] 软删：删掉的记忆不出现在任何查询结果里，但行还在
      —— `test_soft_deleted_memories_disappear_but_stay_in_the_table`（裸查证行还在）
      + `test_soft_delete_is_scoped_to_the_owner`（别人的删不掉）
- [x] 时间衰减：造两条不同时间、同权重的记忆，断言排序符合预期
      —— `test_newer_memories_rank_higher`；曲线本身另有 `test_db_memories.py` 四条
- [x] 容量淘汰：写到上限之上，淘汰的是分值最低的，且是**软删**
      —— `test_overflow_evicts_the_lowest_scoring_memory`（capacity=3 写 4 条）
- [x] alembic 迁移可 upgrade 可 downgrade
      —— `pytest -m pg_db`：106 passed（含零差异门与回退）；开发库已 upgrade 到
      `0007_memories (head)`，新表 11 列在库、审计行 `from 0006_run_usage_by_model`
- [x] `CharAgent` 既有用例全绿：离线 1527 passed / 140 deselected；`-m pg_db` 106 passed

## 开工前要定的

- 衰减函数（建议指数衰减 + 半衰期配成天数）与容量上限 N（建议 50）
  —— **定了**：指数衰减 `0.5 ** (age_days / half_life_days)`；`DEFAULT_HALF_LIFE_DAYS = 30`
  天、`DEFAULT_CAPACITY = 50`，两个都是 `MemoriesRepository` 的构造参数（可配）。
  模块 docstring 写明「这是拍的初始值，等有真实使用数据再调」
- `kind` 要不要更多取值（本票只 `episodic` / `semantic` 两个 —— 保持最小）
  —— **定了**：只两个（`MemoryKind`）；#31 的另两层在枚举 docstring 里写明为什么不做
  （短期 = 对话上下文归会话与快照；程序性 = 另一个量级的事）
- 是否现在就做「用户可见可删记忆」的界面。**建议不做**（本项目没有面向买家的记忆管理面板），
  但要在收口里如实记录「可删」这条当前只有 DB 层能力
  —— **不做**：「可删」当前只有 DB 层能力（`soft_delete`），没有界面、也没有对外的
  删除入口；C13 的工具层会不会挂上去是那张票的事

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `CharAgent/db/schema.py` | **新表** `memories`（11 列 + `(tenant_id, user_id)` 复合索引 + 表/列注释）；「六张 → 七张」文案 |
| `CharAgent/alembic/versions/0007_memories.py` | **新写**：建表 / 回退（手写；列注释与 schema.py 逐字一致） |
| `CharAgent/db/entities.py` | `Memory` 实体 + `MemoryKind` 枚举；「五实体 → 六实体」文案 |
| `CharAgent/db/repositories/memories.py` | **新写**：`recency_score` + `MemoriesRepository`（`add` / `list_for_user` / `soft_delete` / `_prune`）+ `DEFAULT_CAPACITY` / `DEFAULT_HALF_LIFE_DAYS` |
| `CharAgent/db/repositories/__init__.py`、`CharAgent/db/__init__.py`、`CharAgent/__init__.py` | 三层门面同步：仓储包门面导出 `MemoriesRepository` / `recency_score`（与 `message_id_for` 同一条路），`db` 门面与根门面再上浮 `Memory` / `MemoryKind` / `memories`（防漂移用例过） |
| `CharAgent/db/README.md` | 六实体 / 七张表 / 六个取数口；既有迁移表补 0005 / 0006 / 0007；追加路径里 `memories` 从 P2 挪到已实现 |
| `CharAgent/db/database.py`、`CharAgent/alembic/env.py`、`CharAgent/__init__.py` | 「六张 → 七张」「五实体 → 六实体」文案 |
| 测试 | `tests/test_db_memories.py`（**新写**：分值曲线 4 条）· `tests/test_db_store.py`（+8：字段 roundtrip / 隔离 / 去重 / 措辞不同不算重 / 排序 / 软删 ×2 / 淘汰）· `tests/test_db_schema.py`（七张表）· `tests/test_db_entities.py`（+`MemoryKind`）· `tests/test_db_alembic.py`（head 跟到 0007） |

## 实施记录

**「权重」那一半没有数据来源，落地时如实退出了。** 票面写「排序分 = 权重 ×
衰减(now − created_at)」，而落表时发现**没有任何一列装「权重」**：模型不自评重要度
（那是 C13 的取舍：参数越少越不易调错），也还没有访问计数。于是排序分落地为
`recency_score`（只有衰减那一半），`last_used_at` 列为「用得多的排前面」留口 ——
将来引入权重时在这一处相乘。这是**与票面字面不同的一处**，写在这里而不是埋在代码里。

**衰减基准是 `created_at`；去重不重置它（已知边界）。** 去重命中刷新 `updated_at`
（「又被说了一次」留痕）但衰减不重置 —— 一条被反复确认的偏好仍会随时间下沉。
要让重复提及也变新鲜，改法是排序用 `GREATEST(created_at, updated_at)` 或引入权重列
—— 等有真实使用数据再定，现在有一条用例把这个边界钉成事实
（`test_repeating_the_same_content_does_not_add_a_row`）。

**不挂外键是语义决定，不是省事。** 记忆比产生它的对话活得久：挂
`charagent_threads` 的外键要么让删会话把它 CASCADE 掉、要么把来路 SET NULL 掉，
两个都不对。`source_thread_id` / `source_run_id` 只是溯源线索（指向的行没了，
记忆仍然有效）。代价明写：这两列 join 不上时没有约束会提醒你。

**排序在 Python 做。** 指数衰减进 SQL 是一串 `power(0.5, ...)`（半衰期参数还得
一路带进语句），而活记忆条数有上限（容量淘汰兜着，默认 ≤ 50）—— 拉回来排既简单，
又把口径留在可单测的纯函数里。等哪天记忆量真的逼近上限、全量返回开始挤占上下文，
再加检索参数（那是 C13 明说的边界）。

**框架自检当场抓到一处越界（值得记）。** 第一版注释里顺手用了业务例子
（「买家」「收货地址」），`test_agent_provider.py::test_the_framework_never_mentions_the_business`
立刻红 —— 那是本项目「框架不认识业务」的扫源码门。改成中性例子
（「用户」「上次说他换了工作」）。这条纪律不只是规矩，是真的在跑。

**开发库已升级**：`alembic -c CharAgent/alembic.ini upgrade head` → `0007_memories (head)`；
`charagent_memories` 11 列在库，版本表那行 `from = 0006_run_usage_by_model`。

**code-review 之后的三处收紧**：`_prune` 不再返回被淘汰的编号（没人用的出口 ——
用例都是从库里验的）；「谁在前 / 谁先淘汰」的排序键抽成 `_rank_key` 一处
（取回与淘汰两处各写一份迟早会漂）；`recency_score` 改走仓储包门面导出（与
`message_id_for` 同一条路，不再从模块路径进 `db` 门面）。并发窗口（去重与淘汰
是先查后写）记为 add 的已知边界，没有上锁或唯一索引。
