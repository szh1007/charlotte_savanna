# C12 · 长期记忆-a：记忆模型与存储（情景 + 语义两层）

**Status:** todo

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

- [ ] 表建起来，每列有 `comment`，`test_db_schema.py` 的强制用例通过
- [ ] 仓储的每个读方法都带 `(tenant_id, user_id)`；**换个用户查不到别人的记忆**（否定断言）
- [ ] 软删：删掉的记忆不出现在任何查询结果里，但行还在
- [ ] 时间衰减：造两条不同时间、同权重的记忆，断言排序符合预期
- [ ] 容量淘汰：写到上限之上，淘汰的是分值最低的，且是**软删**
- [ ] alembic 迁移可 upgrade 可 downgrade
- [ ] `CharAgent` 既有 1502 个用例全绿

## 开工前要定的

- 衰减函数（建议指数衰减 + 半衰期配成天数）与容量上限 N（建议 50）
- `kind` 要不要更多取值（本票只 `episodic` / `semantic` 两个 —— 保持最小）
- 是否现在就做「用户可见可删记忆」的界面。**建议不做**（本项目没有面向买家的记忆管理面板），但要在收口里如实记录「可删」这条当前只有 DB 层能力

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
