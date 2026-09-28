# 41 · 业务侧：跑分环境接线（假商城 + 内存记录层 + 敏感值清单）

**Status:** done

**Type:** task

**Blocked by:** 无（可与 39 / 40 并行开工）

**上游:** L4 规划期决策（假商城、内存记录层、打真模型）；issue 38 §八第 3 条；`CharAgent/docs/DESIGN.md` #63（测试与评估的分界）

## 现状（2026-09-28 核实）

跑分要的零件**大多在，但全在测试上下文里**，跑分器（非 pytest 上下文）拿不到：

| 零件 | 在哪 | 跑分器能用吗 |
|---|---|---|
| 假商城 18 端点 + 样本数据 | `CharApp/tests/conftest.py:88-312`（`PRODUCT` / `ORDER_LIST` / … / `mall` fixture / `mock_all` / `client`） | ❌ 是 pytest fixture 与测试模块常量 |
| 内存记录层 | `CharAgent/tests/doubles.py:509-571`（`FakeRecordDatabase`） | ⚠️ 是**框架的测试替身**，业务 import 它等于绑框架内部结构 |
| 敏感值清单（v4 判据要用） | **不存在**。最接近的只有 `test_log_redaction.py:39-40` 的两个值 | ❌ |
| 模型装配 | `CharApp/minimall/service.py:221-227` → `CharAgent/client/app.py:235-258` | ✅ 生产代码，直接可用 |

**装配路径已经验证过**（`CharApp/tests/test_compaction.py:46-68`、`test_recording.py:56-107`）：

```python
service = MinimallService(client=client, model=model,
                          saver=InMemoryCheckpointSaver(), compaction=...)
context = build_context(BUYER_ID, "web", tenant_id=TENANT_WEB)
session = await service.session_for(context, event_sink=..., redact=False)
await session.ask("...")
```

`database` 给了就**自动挂上 `ConversationRecorder`**（`service.py:407` / `:410-426`），不需要跑分器做别的。

## 本片要做的四件

### 一、把测试上下文里的可复用件抽成生产模块

跑分器不是 pytest 用例（它要落报告、要 `compare`、要传参数），所以它吃不到 fixture。**抽三样**：

| 抽什么 | 从哪 | 抽到哪（倾向） |
|---|---|---|
| 样本数据（12 张表） | `tests/conftest.py:88-231` | `CharApp/eval/fixtures.py` |
| 假商城构建（`mall` 那套 respx 路由） | `tests/conftest.py:247-312` | 同上（fixture 改为调它） |
| 内存记录层 | `CharAgent/tests/doubles.py` | 见第二条 |

方向是**测试 → eval 模块**（生产不依赖测试），不是反过来。`conftest.py` 改成从新模块 import，161 个现有用例的行为一个不变（抽的时候只搬常量与构建函数，不改断言）。

### 二、`FakeRecordDatabase` 提升为框架公共 API

今天业务要用它，只能 `from CharAgent.tests.doubles import FakeRecordDatabase` —— 依赖方向仍是单向的（`CharApp → CharAgent`），但**绑到了框架的内部测试结构**上，而 issue 40 的框架侧自证用例也要用同一份。

> **2026-09-28 更正（issue 40 已落地）**：那句「issue 40 的自证也要用同一份」**没有兑现** —— 40 的自证走的是 `LoopResult.turns`（内存那条路），没有碰记录层。于是「同一份事实两条路建得出来」这句话**只有本片能证**（本片交付物 #5 的「按 run_id 筛」正是那条路）。提升 `FakeRecordDatabase` 的理由仍成立（它已被 `test_db_recorder.py` / `test_server_approval.py` 多处复用），但别再拿 40 当第二条理由。

**倾向：框架把它提升为公共 API**（如 `CharAgent/db/testing.py`，`tests/doubles.py` 改为从那里转发）。理由：它已经被多处复用（`test_db_recorder.py:878-928`、`test_server_approval.py` 六处），"框架提供测试替身"是常规做法；提升之后框架内部与业务用**同一份**，不会漂。

**但不进根门面**（与 `client` / `server` 同：它是给测试与离线跑分用的，不是业务主力 API）。

### 三、三个坑的处置（都是 2026-09-28 核实出来的）

| # | 坑 | 处置（倾向） |
|---|----|------------|
| 1 | `FakeRecordSession.scalars` **不过滤 WHERE 也不排序**（`doubles.py:340-352`，只有 `Message` 被过滤 `hidden`）→ `list_for_run(run_id)` 在假库上返回**库里全部** tool_calls | **跑分器自己按 `call.run_id` 筛**（零框架改动）。**不要**改假库的过滤行为 —— 现有用例可能依赖"返回全部" |
| 2 | `FakeRecordDatabase` **没有 `dispose`**，而 `MinimallService.aclose()` 会调它（`service.py:428-440`）→ `AttributeError`，且是在关掉 model / saver / client **之后**才炸 | 提升为公共 API 时**补齐 `dispose` 的空实现**（一行）。比"跑分器记得别调 aclose"可靠 |
| 3 | 假库不校验外键 → **无影响**，但要记下来：ticket 24 那条死路（`saver` 是 Postgres 时 `database=None` 会外键失败）在 `InMemoryCheckpointSaver()` + 假库这只组合上**不成立** | 写进跑分器的装配函数 docstring |

### 四、敏感值清单

v4 的"不复述地址姓名"判据（issue 45）要拿真实值去回答文本里搜。**清单从样本数据派生**（单一来源），不另写一份：

| 敏感名 | 样本真值 | 处置 |
|---|---|---|
| `phone` | `"13800000003"` | 进清单 |
| `email` | `"buyer3@example.com"` | 进清单 |
| `receiver_name` | `"张三"` | 进清单 |
| `detail` | `"文三路 100 号"` | 进清单 |
| `payment_password` | `"135791"`（测试常量） | 进清单（**永远不该出现**） |
| `balance` | `"9500.00"` | **排除**（用户 2026-09-28 定：余额是助手被设计来做的事，现有 4 条用例要求复述它） |
| `balance_remaining` | `"8101.00"` | **排除**（同上） |
| 订单号 | `"202609191230450000031234"` | **排除**（所有订单号都不敏感，已有两条用例守着它活着） |

**要有一条断言守着清单与 `LOG_FIELDS` 不漂** —— 同型的先例在 `test_log_redaction.py:124-138`（`declared - {"payment_password"} <= payload_names`）。清单是只读常量，不是配置（照 `log_redaction.py:31` 那句"名单是代码，不是配置"）。

## 交付物

| # | 内容 |
|---|------|
| 1 | `CharApp/eval/fixtures.py`：样本数据 + 假商城构建函数 + 敏感值清单 |
| 2 | `tests/conftest.py` 改为从新模块 import（**161 个用例行为不变**） |
| 3 | 框架侧：`FakeRecordDatabase` 提升为公共 API（含 `dispose` 空实现），`tests/doubles.py` 转发 |
| 4 | 跑分环境的装配函数：`MinimallService` + `InMemoryCheckpointSaver` + 假库 + 假商城（docstring 写明三个坑） |
| 5 | 读回：按 `run_id` 筛的工具轨迹（**不用假库的 `list_for_run`**）。**范围要看清**：这里说的是**单次运行的轨迹**（一条一条的调用行）；**跨工具的聚合**（次数 / 成功率 / 平均耗时）走 issue 39 的 `summarize_by_tool` —— 那是**一个查询面**，别在这里再写一套聚合（39 的验收第 4 条点的就是这件事） |
| 6 | 用例：清单与 `LOG_FIELDS` 对齐；装配函数能真跑一次问答并读回轨迹 |

## 验收

- [x] 一次问答跑完能读回**该次运行**的工具轨迹 —— 只读回这一次的，不是全库
- [x] `service.aclose()` 不炸（假库有了 `dispose`）
- [x] 敏感值清单与 `LOG_FIELDS` 有断言守着不漂
- [x] **`CharApp/tests` 的既有用例一条不变红**（抽模块是搬迁，不是重构）
- [x] 框架侧 `CharAgent` 的既有用例一条不变红（提升 `FakeRecordDatabase` 是搬迁 + 补一个空方法）
- [x] `FakeRecordDatabase` 提升后**不进根门面**

## 要定死的开放决策

| # | 决策 | 倾向 |
|---|------|------|
| 1 | 框架侧公共 API 放哪个模块 | `CharAgent/db/testing.py`（与 `db/` 同包，"要用库的东西"这一层） |
| 2 | 跑分结果是 pytest 用例还是独立入口 | **独立入口**（要落报告 / compare / 传参）。`pytest` 里只留框架侧的自证用例（issue 40） |
| 3 | 敏感值清单是"从样本派生"还是"独立声明" | **从样本派生**（单一来源）。清单与样本漂了就等于判据失效 |
| 4 | 假商城跑分时用不用 `assert_all_mocked=True` | **用**（照 `conftest.py:276-288` 的既有取法）—— 漏铺的端点要当场炸，不是静默返回空 |

## 实施记录（2026-09-28）

**四件全部落地，六条验收框全勾。**

| 交付物 | 落点 |
|---|---|
| 1 样本 + 假商城 + 敏感值清单 | `CharApp/eval/fixtures.py`（`build_mall()` / `mock_all()` / `SENSITIVE_VALUES`） |
| 2 `conftest.py` 改为 import | `CharApp/tests/conftest.py`（`__all__` 声明转发面，fixture 改调 `build_mall`） |
| 3 假库提升为包内公共 API | `CharAgent/db/testing.py`（含 `dispose`），`tests/doubles.py` 转发 |
| 4 装配函数 | `CharApp/eval/harness.py`：`open_harness(model)` → `EvalHarness` |
| 5 按 `run_id` 筛的工具轨迹 | `EvalHarness.calls_of(run_id)`（**不用**假库的 `list_for_run`） |
| 6 用例 | `CharApp/tests/test_eval_fixtures.py`（3 条）+ `test_eval_harness.py`（6 条） |

### 与票面的出入

| 票面 | 实际做法 | 为什么 |
|---|---|---|
| §一抽「样本数据」与「假商城」两样 | 还搬了 `AGENT_BASE_URL` / `TOKEN` / `BUYER_ID` / `PAYMENT_PASSWORD` / `ORDER_NO` / `agent_url` | 样本与假商城都引用它们（`PROFILE["id"]`、`WRITE_ENDPOINTS` 的键、`build_mall` 的地址），留在 conftest 里就成了「从测试 import 生产常量」 |
| §二只点名 `FakeRecordDatabase` | 连 `FakeRecordSession` / `record_message` / `record_thread` 一起搬 | 那个类离了会话替身不成立；两个造行的与假库同源（它的 docstring 例子就在用），分开摆等于把一份东西劈两半 |
| §四「断言守着不漂」（先例是子集断言） | 写成**等式**：`set(SENSITIVE_VALUES) == masked - {"balance"}` | 先例那句 `declared - {"payment_password"} <= payload_names` 是子集，漏一个不报；这里两张表的差**只有余额**一格，等式说得清且更严 |
| 决策 2「跑分结果是独立入口」 | 本片**没建**入口 | 入口要题集（42）与判据（42）才成立 —— 本片交的是它踩的地基 |

### 两轴复核（`/code-review`）后的修补

两个轴各一个子代理，审的是工作区（`CharApp/eval/` 与 `CharAgent/db/testing.py` 是新文件，不在 `git diff` 里，另行指读了）。

**标准轴：1 条硬违规 + 1 条小项 + 5 条判断项，采纳 3 条**：

| 复核意见 | 处置 |
|---------|------|
| **硬**：`db/testing.py` 里一个全角句号（项目 §4.9 明写标点一律英文） | **改**（它是从 `doubles.py` 逐字搬过来的旧毛病，但文件是新的，即在范围内） |
| `EvalHarness.context` 有 `Args` 无 `Returns`，`session` 两者都无 | **改**：补齐 |
| `doubles.py` 的转发不一致：两个类转发、两个造行的不转发，「只跟着假库走」这条理由对两者同样成立 | **改**：四个一起转发 + 加 `__all__`（顺带把三处测试 import 改回原样，转发这才有消费方） |
| `testing.py` 里实体→表的映射写了五遍（`_TABLES` / `_KEYS` / `_PK_COLUMNS` / `_rows_of` / `rows_of`） | **保留**：这是搬迁前就有的（`doubles.py` 原文如此），而本片对它的承诺是「行为一字不改」—— 顺手改五处映射会让 diff 不再能证明这件事 |
| `calls_of` 有 Feature Envy（跑分器伸手进记录层筛数据） | **保留**：票据 §三坑 1 点名「**跑分器自己按 `call.run_id` 筛**（零框架改动），不要改假库的过滤行为」 |
| `testing` 这个名字（本仓既有的叫法是 `doubles`） | **保留**：决策 1 定的就是它；`tests/doubles.py` 仍在，两者是不同的东西（一个模块名，一个测试侧替身集合） |
| 业务测试仍从 `CharAgent.tests.doubles` 取假库 | **保留**：业务借框架的测试替身是 PRD §5 既定的接缝（与 `mock_llm` / `trace_assertions` 同一类） |

**规格轴：2 条「只做到一半」+ 2 条范围蔓延，采纳 3 条**：

| 复核意见 | 处置 |
|---------|------|
| §二那句「『同一份事实两条路建得出来』**只有本片能证**」没人证 —— `calls_of` 只读记录层，没与内存那条（`LoopResult.turns`）对照过 | **改**：补一条用例（一次调两个工具，同一轮并行 —— 记录层靠时间戳排序，那是这条路上唯一可能分叉的地方），逐字段比 `ToolFact` 与调用行 |
| 交付物 #3「`doubles.py` 转发」与字面有落差（见上） | **改**：补齐转发 |
| `CharApp/eval/__init__.py` 里写「独立入口的活（`python -m CharApp.eval ...`）」—— **那个命令不存在** | **改**：改成「入口与题集在 issue 42 之后那几片」（本片新写的句子不该陈述一件当时为假的事） |
| `EvalHarness.context` / `session` / `routes` 与「参数原文」那条用例属前瞻（42–46 号票对 harness API 零引用） | **保留**：三样今天都有消费方（`routes` 用来证「工具真打到商城了」），而「出口是什么」「租户用哪个」这类决定该有**一处**说了算 —— 摊到四个调用点去各自决定，正是 issue 30 那类半边生效的温床 |

### 跑过的用例（收尾那一遍）

| 命令 | 结果 |
|------|------|
| `pytest`（CharApp 全量） | **241 passed**（基线 232：本片 +9，既有 232 条一条不变红） |
| `pytest`（CharAgent 全量） | **1370 passed, 132 deselected**（基线 1369：本片 +1 —— 根门面那条防漂断言） |
| `ruff check .` + `format --check` | All checks passed |

### 给 issue 42 / 43 / 44 / 45 的话

- **读轨迹只有一处**：`harness.calls_of(run_id)`（单次运行的调用行）。**次数 / 成功率 / 平均耗时那类聚合走 issue 39 的 `summarize_by_tool`**，别在这里再写一套（本片交付物 #5 后半段点名的就是这件事）。
- **跑分环境这样装**：`async with open_harness(model) as harness:` —— 它自带假商城 + 内存快照 + 假记录库，两处照生产读环境变量（`CHARAPP_THINKING` / `CHARAPP_CONTEXT_*`），报告头部那块配置快照记的就是它们。**模型的所有权交出去**（收尾时连它一起关），所以**为每一跑造一份**，别共享。
- **43 的恢复路**：`harness.service` 就是那台 `MinimallService`（`session_for` 在它上面），「重新装配一次」照 HTTP 那条路做即可；挂起检测走 `ToolCallsRepository(harness.records).list_pending_approvals(thread_id)`（假库不过滤 WHERE，但挂起那条查询靠 `PendingAwareSession` 在 Python 侧补过语义）。
- **44 / 45 要加的注入口还没开**：`open_harness(model)` 目前只收一个模型 —— 裁剪钩子（44）与 prompt 版本（45）要么加参数、要么加 `MinimallService` 上的字段，本片**没有**替它们先开（免得开错形状）。`harness.routes` 是「这一跑压根没打出去」那条断言的取数口。
