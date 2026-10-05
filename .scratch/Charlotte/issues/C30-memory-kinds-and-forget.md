# C30 · 长期记忆-c：kind 行为（替换型）、`forget` 与来源记录

**Status:** done

**Type:** feature

**Blocked by:** C12（表与仓储）、C13（工具与装配）

**上游:** 用户 2026-10-05 的四条决定（见下），接在 C13 收口的真机观察上（「让他切换风格，他就切换了」—— 但换风格只是**新增**一条，旧的仍活，语义上应该是替换）

## 用户的四条决定（2026-10-05）

1. **一张票**：存储、工具、测试全在 C30。
2. **`forget` 由模型自行判断删哪条** —— 不要求「content 精确匹配」；模型能指认出目标即可。
3. **`remember` 现在就同时记 `source_thread_id` 与 `source_run_id`**（不允许「只记 thread 级」的过渡态）。
4. **不做 prompt v9** —— 风格 / 称呼在系统提示词里不需要额外指引：v8 的「想起来的偏好该兼顾就兼顾」已覆盖生效路径；「改主意记新的、旧的自动失效」是仓储层的自动行为；`forget` 的时机写进工具描述。**prompt 一字不动**。

## 一、kind 行为：替换型 vs 累积型

记忆分两种**行为语义**（kind 分的是行为，不是主题 —— 不为「地址」「口味」这类主题各开 kind）：

| 行为 | kind | 表现 |
|------|------|------|
| **累积型**（多条并存，只增） | `episodic` / `semantic` | 「寄到公司」与「对花生过敏」同时有效 |
| **替换型**（同一时刻只留一条，新写顶掉旧的） | **`style`**（回答风格）/ **`nickname`**（称呼） | 写「冷酷」→ 旧的「可爱」软删 |

- **kind 即槽**：一个替换型概念一个 kind（风格与称呼要能并存，不能同槽）。将来要「风格拆多维度」再引入 slot 列 —— 现在不做。
- **行为表**：`KIND_BEHAVIORS: dict[MemoryKind, KindBehavior]`（`label` 中文标签 + `replaces_previous` 布尔），放 `db/entities.py` —— 与 state.py 的「枚举 + 单独规则表」同款。`_KIND_LABELS` 从 `tool/memory.py` 迁进来（单一来源），守卫用例「每个 kind 都有行为定义」。
- **写入语义**（`MemoriesRepository.add`）：先按老规矩查「同内容活行」（有 → 只刷新 `updated_at`）；没有且 kind 是替换型 → **软删同 `(tenant, user, kind)` 的全部活行**（`deleted_at` = 此刻，旧风格何时被换掉可查）→ 插新行。累积型照旧。
- **零迁移**：kind 是自由字符串列，加取值不动表。

## 二、`forget` 工具（第 3 个记忆工具）

- **形态**：`forget(memory_id: str)` —— 按**编号**删（`memory_id` 前 8 位即为编号；也接受完整编号，前缀匹配天然覆盖）。
- **编号从哪来**：`recall` 每条显示 `(编号 3f2a1b7c)`。模型先 recall 拿到列表 → 自行判断删哪条 → 按编号调 forget。
- **为什么不做「按 content 模糊匹配」**：那需要相似度判断 —— C12 明确不做（判错把不该删的删了，代价远大于多留一条）；编号方案是「模型自己判断 + 系统零猜测」。
- **安全**：前缀只匹配**活行 + 本人**；命中多条（几乎不可能，8 位 hex）→ **不删**，回一句「编号不唯一」。
- **仓储**：加 `list_by_id_prefix(prefix, *, tenant_id, user_id)`（返回活行列表）；删除复用既有 `soft_delete`（重删幂等）。工具层三步：查 → 判（0/1/>1）→ 删。
- **取消风格的完整路径**：说「不要可爱了」→ 模型 recall 拿编号 → forget 掉 → 槽变空。

## 三、两处来源记录（`remember` 写入时）

| 列 | 取自 | 为什么 |
|----|------|--------|
| `source_thread_id` | 闭包参数（`build_memory_tools` 新增必填 `thread_id`，装配处传 `context.thread_id`） | 装配期就定的事实，与 `tenant_id` / `user_id` 同族 |
| `source_run_id` | **执行期** `structured_logging.current_ids().run_id` | 记录层那一行的编号 —— **现成设施**：`ChatSession.ask` 的 `log_context` 把整段运行包住，run 编号在 `_begin_run` 之后补绑；没绑时是 None（如直接单测工具），NULL 即如实 |

- 上轮「没有通道」的判断只查了 `RunContext` 与 `execute_tool`，漏了日志上下文 —— **不需要新机制**，`tool/memory.py` import `current_ids` 即可（`structured_logging` 零框架依赖，无环）。

## 四、`last_used_at`：先记录、不进排序

- `recall` 取回的行批量刷 `last_used_at = now`（仓储加 `touch_used(memory_ids, *, moment=None)`；工具里读完再刷，重试幂等）。
- **不改变任何现有行为**：不进排序、不进淘汰 —— 现在的 recall 是全量返回，「所有行同刷」没有区分度；它从今天起积累使用证据，等「recall 能返回子集」那天再接进打分。
- **不打 `writes` 注解**（审计性写入，不占买家写预算，理由同 remember）。

## 验收

- [x] 替换型：写第二条 `style` → 旧的**软删**（行还在、`deleted_at` 有值），`recall` 只见新的一条
      —— `test_a_replacing_kind_supersedes_the_previous_one`（真库）+ `test_changing_a_style_supersedes_the_previous_one`（工具路径）
- [x] 槽互不干扰：写 `nickname` 不动 `style`；写累积型不动任何槽
      —— `test_a_replacing_kind_does_not_touch_other_kinds`（真库）+ `test_a_style_does_not_touch_the_nickname`
- [x] 同内容重复写（替换型/累积型）→ 仍是一条，只刷 `updated_at`（去重优先于替换）
      —— `test_repeating_the_same_replacing_value_keeps_one_row`
- [x] `forget`：按 8 位编号删掉一条 → `recall` 不再返回；不存在的编号 → 可操作回话；别家的编号删不动
      —— 框架侧三态用例（删除 / 未知 / 撞多条）+ `test_forget_removes_the_memory_the_model_pointed_at`
      + `test_forget_never_removes_someone_elses_memory`
- [x] `remember` 落库后 `source_thread_id` / `source_run_id` 有真值；无运行时 run 列如实为 NULL
      —— `test_remember_records_the_source_pair`（log_context 包住）+ `test_without_a_run_context_...`；
      真机可证（库里新行的两列都非空）
- [x] `recall` 后 `last_used_at` 被刷新，且**排序/淘汰不变**
      —— `test_touch_used_stamps_when_a_memory_was_last_recalled`（刷两种、顺序仍按 created_at）
- [x] 行为表守卫：每个 kind 都有 `label` 与 `replaces_previous`；标签唯一
      —— `test_every_kind_has_a_behavior_defined` + `test_the_replace_kinds_are_the_expected_ones`
- [x] 上游全绿：CharAgent 离线 **1551** / `-m pg_db` **111**；CharApp **451**；ruff 全清
- [x] 真机（**用户自验**，2026-10-05 晚，重启服务后）：库里 `nickname` 三条
      「小鸡毛」→「小白」→「小金毛」前两条**软删**、最后一条活 —— 替换型端到端成立；
      三条的 `source_thread_id` / `source_run_id` 均非空（新代码生效）

## 不做什么

- **不做 prompt v9**（用户决定，理由见上）。
- **不做「风格拆多维度」的 slot 列**（kind 即槽够用；真需要时加列）。
- **不做界面**（记忆可看可删的页面不属本票）。
- **不改并发模型**（去重/覆盖/淘汰仍是先查后写；重叠时替换型最多短暂出现两条活行 —— 已有边界，docstring 记录）。
- 数据面：老风格条目用户已手动清理，**本票不带迁移**。

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `CharAgent/db/entities.py` | `MemoryKind` 四值（+`STYLE` / `NICKNAME`）+ `KindBehavior` 与 `KIND_BEHAVIORS` 行为表（枚举 + 单独规则表，与 state.py 同款） |
| `CharAgent/db/repositories/memories.py` | `add` 的替换分支（同 kind 活行软删，去重优先）；`list_by_id_prefix`（`startswith(autoescape=True)`）；`touch_used`（带归属过滤） |
| `CharAgent/tool/memory.py` | 第三个工具 `forget`（按编号，命中多条拒绝）；`recall` 每条带短编号 + 刷 `last_used_at`；`remember` 写 `source_thread_id`（闭包）+ `source_run_id`（`current_ids()`）；`build_memory_tools` 加必填 `thread_id`；种类标签改从行为表取（`_KIND_LABELS` 取消） |
| `CharAgent/db/__init__.py`、`CharAgent/__init__.py` | 门面导出 `KIND_BEHAVIORS` / `KindBehavior` |
| `CharAgent/tests/` | `test_db_entities.py`（+3：四值 / 行为表齐全 / 替换型名单）；`test_db_store.py`（+5：替换软删 / 槽隔离 / 去重优先 / 前缀查 / touch 只记账）；`test_tool_memory.py`（重写为 22 条：含来源两列、编号、forget 三态） |
| `CharApp/minimall/provider.py` | 记忆装配多传 `thread_id=context.thread_id`（**装配期才拿得到，事后补不回来**） |
| `CharApp/minimall/redaction.py` | `TOOL_PHRASES` +forget（「正在忘掉这一条 / 忘掉了 / 没能忘掉」） |
| `CharApp/minimall/scoping.py` | memory 组 +forget；关键词 +「忘掉」「别记」 |
| `CharApp/tests/` | `conftest.py`（`TOOL_NAMES` +forget；`FakeMemoryStore` +替换语义 / `list_by_id_prefix` / `soft_delete` / `touch_used`；`FakeMemory` +`last_used_at`）；`test_tools.py`（+4：换风格顶旧 / 槽隔离 / forget 全路径 / 别人删不动；`[偏好]`→`[事实]`）；`test_provider.py`（`EXPECTED_PARAMS` +forget）；`test_eval_subject.py`（`EVAL_SKIPPED_TOOLS` +forget） |
| 文档 | `CONTEXT.md`（「长期记忆」词条补替换型 / forget / 来源两列）、本票、`PLAN.md` 进度 |

## 实施记录

**术语先掰正**（用户原话里「覆盖」用反了方向）：按**行为**命名两类 ——
**累积型**（`episodic` / `semantic`，多条并存、只增）与**替换型**（`style` /
`nickname`，同一时刻只留一条、新写顶掉旧的）。**kind 分的是行为不是主题**：
不为「地址」「口味」各开 kind；一个替换型概念一个 kind（kind 即「槽」—— 风格与
称呼因此是两个 kind，能并存）。

**`forget` 落成「按编号删」**（用户要求「模型自行判断删哪条、不要求 content
精确匹配」）：`recall` 每条显示 `memory_id` 前 8 位作编号，模型从列表里自己挑，
`forget(memory_id=…)` 前缀匹配 + 「命中多条就不删、请抄更长」的兜底。**不做内容
模糊匹配** —— 那需要相似度判断（C12 明确不做），判错就是删错行，代价远大于让
模型多看一眼编号。

**`source_run_id` 是上轮判错的更正**：上一轮说「工具拿不到 run_id，要开新通道」
—— 只查了 `RunContext` 与 `execute_tool`，漏了日志上下文。事实是
`structured_logging.TraceIds` 本来就带 `run_id`（语义正是「记录层那一行」），
`ChatSession.ask` 的 `log_context` 把整段运行包住、`_begin_run` 之后补绑 ——
工具执行时 `current_ids().run_id` 直接读得到，**零新机制**。`source_thread_id`
则走闭包（装配期事实，不依赖运行期绑定）。

**`last_used_at` 先记账、不进排序**（用户问「为什么不记录」后的修正）：`recall`
取回后批量刷（仓储 `touch_used`），但排序与淘汰**不读它** —— 现在全量返回没有
区分度，从今天起积累使用证据，等 recall 能返回子集那天再接进打分。

**prompt 一字不动**（用户判断，采纳）：v8 的「想起来的偏好该兼顾就兼顾」已覆盖
风格/称呼的生效；「改主意记新的、旧的自动失效」是仓储层行为、模型无需知道；
`forget` 的时机写进工具描述。原计划的 v9 取消。

**测试**：CharAgent 离线 1551 passed（+10）、`-m pg_db` 111 passed（+5）；
CharApp 451 passed（+4）；ruff 全清。

## 真机（用户自验，2026-10-05 晚）

代码改动落在客服服务进程里（端口 1007）—— 用户自行重启服务后自己验的（服务日志
18:35 起多轮真实运行）。库里可证的：`nickname` 三次更新「小鸡毛」→「小白」→
「小金毛」，前两条**软删**、最后一条活 —— 替换型的端到端证据；三条的
`source_thread_id` / `source_run_id` 都非空。此前那些条目用户已自行清理，库里现存
即上述三条。**本轮未做 forget 的真机复验**（行为由两侧离线用例与替换路径的同款
软删守着）。

---

## 召回演进建议（2026-10-05 记，**不在本票实施** —— 押后等触发条件）

C30 落地之后，「召回」这条链还剩最后一段没走：`recall` 仍是**全量返回**（每个
用户 ≤ 容量上限 50 条）。子集化分三层，依赖逐个变重；什么时候做看触发条件，
不为了做而做。

### 一条硬规则（无论做哪层先记住）

`style` / `nickname` 是**全局生效的人设** —— 「回答可爱一点」与当前问什么无关，
每次回答都要遵守，**绝不能因为「和这个问题不相关」被检索过滤掉**。判据现成：行为表
`KIND_BEHAVIORS.replaces_previous` —— **替换型永远全带**（每 kind 至多一条），
子集化只裁累积型（`episodic` / `semantic`）。

### 三层

| 层 | 选子集的依据 | 依赖 | 规模 |
|---|---|---|---|
| **L1 截断** | 按现有分值（时间衰减）取 top-K，替换型全带 | 零（纯框架内） | 小票 |
| **L2 使用加权** | `last_used_at` 进分值（「被取回过」的行加分） | L1 先做（全量下同刷、无区分度；截断后「谁进了前 K」才是信号） | 小票，但要等使用数据 |
| **L3 语义检索** | `recall(query)` → 相似度 × 时间融合取 top-K（#33 的「语义 + 时间加权」） | embedding 从业务注入 + 记忆行加向量列（迁移）+ prompt 教模型传 query | 大票 |

### L3 的分层形态（真正难的那层）

记忆表在**框架**（不知道 bge-m3 / Milvus 的存在），向量能力在**业务**。正确形态是
框架定协议、业务注入 —— 与 `ChatModel` / `ToolProvider` / `Checkpointer` 同一套
模式：

- 框架：可选注入的排序 / 嵌入协议（没注入 = 纯时间排序，即现状形态）；
- 向量存哪：新列 `embedding` JSONB（可空）最省 —— 50 条 × 1024 维在 Python 里算
  余弦是毫秒级，不引 pgvector、不碰 Milvus（记忆由框架写入，业务侧另建索引就成
  **双写** —— C12 明确反对的那件事）；
- 业务：复用 `knowledge/embeddings.py` 的 bge-m3 惰性单例注入。

### 触发条件与开放决策

- **现在不做**：全量召回在「每用户 ≤ 50 条、真实几条到十几条」下并不痛（几百
  token）；L1 的即时价值是铺管道（把容量上限与上下文占用解耦），不是解当下之痛。
- **L2 的触发**：`last_used_at`（C30 起记账）攒出真实分布之后 —— 那时权重才不是
  拍的。
- **L3 的触发**：单用户累积型逼近容量上限，或真要演示「按需想起」。
- 开工时再拍：L1 的 K 值（建议 12）；L2 的加成形态与权重；L3 的向量列选型
  （JSONB 先行 vs pgvector）。
- 已有边界文字在 `tool/memory.py` 的模块 docstring（「什么时候该加 query」那
  段）—— 本节是它的完整展开。
