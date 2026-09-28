# 41 · 业务侧：跑分环境接线（假商城 + 内存记录层 + 敏感值清单）

**Status:** ready-for-agent

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
| 5 | 读回：按 `run_id` 筛的工具轨迹（**不用假库的 `list_for_run`**） |
| 6 | 用例：清单与 `LOG_FIELDS` 对齐；装配函数能真跑一次问答并读回轨迹 |

## 验收

- [ ] 一次问答跑完能读回**该次运行**的工具轨迹 —— 只读回这一次的，不是全库
- [ ] `service.aclose()` 不炸（假库有了 `dispose`）
- [ ] 敏感值清单与 `LOG_FIELDS` 有断言守着不漂
- [ ] **`CharApp/tests` 的既有用例一条不变红**（抽模块是搬迁，不是重构）
- [ ] 框架侧 `CharAgent` 的既有用例一条不变红（提升 `FakeRecordDatabase` 是搬迁 + 补一个空方法）
- [ ] `FakeRecordDatabase` 提升后**不进根门面**

## 要定死的开放决策

| # | 决策 | 倾向 |
|---|------|------|
| 1 | 框架侧公共 API 放哪个模块 | `CharAgent/db/testing.py`（与 `db/` 同包，"要用库的东西"这一层） |
| 2 | 跑分结果是 pytest 用例还是独立入口 | **独立入口**（要落报告 / compare / 传参）。`pytest` 里只留框架侧的自证用例（issue 40） |
| 3 | 敏感值清单是"从样本派生"还是"独立声明" | **从样本派生**（单一来源）。清单与样本漂了就等于判据失效 |
| 4 | 假商城跑分时用不用 `assert_all_mocked=True` | **用**（照 `conftest.py:276-288` 的既有取法）—— 漏铺的端点要当场炸，不是静默返回空 |
