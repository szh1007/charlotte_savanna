# 43 · 业务侧：挂起题的模拟确认（跑分器自动走完下单与代付）

**Status:** done

**Type:** task

**Blocked by:** issue 41（跑分环境接线）

**上游:** L4 规划期决策（跑分器自动模拟买家确认）；**ADR-0014**（挂起态的家是 `charagent_tool_calls` 那一行）；**ADR-0015**（密码走一次性载荷，永不落库）；issue 34（挂起-恢复的机制）；issue 38 §六第 3 条

## 为什么有这一片

`place_order` 与 `pay_my_order` 在护栏里返回 `requires_approval`（`CharApp/minimall/guardrail.py`），运行停在 `waiting_user`、**不产生 final 回答**。20 道题里凡"下单 / 代付"的都会挂住 —— 跑分器不处理它，那几道题的 `outcome` 永远是 `suspended`，**报告会缺掉 L3b 最该讲的那一块**。

**这与商城真假无关**：护栏是业务侧的，换假商城照样挂。

## 现状（2026-09-28 核实）

**恢复这条路已经通了，且是"重新装配一次"的形状**：

| 零件 | 位置 | 事实 |
|---|---|---|
| `ChatSession.resume` | `CharAgent/client/session.py:406-410` | 签名 `resume(*, run_id: str \| None = None, approval: Approval \| None = None) -> LoopResult \| None`。**它本身不带载荷** |
| `Approval` | `CharAgent/agent/utils/types.py:176-215` | `Approval.approve()` / `Approval.reject(reason)`。docstring 明写"恢复必须带上它 —— 没有结论就补做等于框架替人按了确认键" |
| **载荷怎么进去** | `CharAgent/server/app.py:432-436`（第 5、6 步） | HTTP 那条路的做法：`approve` 时把 `data` 并进**本次运行的上下文**，然后**让业务用这次的上下文重新装配一次会话**（"一次性载荷要进工具的闭包"） |
| 挂起态的家 | `ToolCallsRepository.list_pending_approvals(thread_id)` | `db/repositories/tool_calls.py:292-322`。ADR-0014：挂起不建审批表，那**一行**就是审批单 |
| `MinimallService` 的恢复面 | `CharApp/minimall/service.py` | **没有专门的恢复路径** —— 它只有 `session_for(context, ...)`。所以跑分器照 HTTP 那条路的形状自己组：新上下文 → 重新装配 → resume |
| 挂起时的会话状态 | issue 38 §六 / `SessionRegistry._busy` | 挂起时 run 会 `release`，但框架侧已把语义扩成"运行中**或**有未决挂起"，且判据落 PG。**进程内直连不经过 `SessionRegistry`**（那是 HTTP 服务层的闸门），跑分器不受它影响 |

## 本片要做的两件

### 一、跑分器的"挂起 → 模拟确认"流程

形状（**与 HTTP 那条路同构**，不是自创一条）：

```python
# 第一段：问一句，可能挂起
session = await service.session_for(context, event_sink=..., redact=False)
result = await session.ask(question)

# 判据走 ADR-0014 那条：挂起态的家是那几行调用
pending = await ToolCallsRepository(database).list_pending_approvals(thread_id)

if pending:
    # 模拟买家本人点了「确认」：
    # 1. 组载荷 —— 只增不覆盖（与 server/app.py:432-433 同一口径）
    payload = {**context.payload, "payment_password": _password()}
    resume_context = dataclasses.replace(context, payload=payload)
    # 2. 用这次的上下文重新装配一次会话（一次性载荷要进工具的闭包）
    session2 = await service.session_for(resume_context, event_sink=..., redact=False)
    # 3. 给结论，恢复（沿用挂起那次的 run_id —— issue 33 定案）
    result2 = await session2.resume(run_id=run_id, approval=Approval.approve())
```

**三处必须照抄 HTTP 那条路的语义，不要另创一套**：

- **载荷是"只增不覆盖"**（`server/app.py:432-433` 与 ADR-0015 的补记）—— 别整个替换 `payload`，那会把 `user_id` 弄丢
- **重新装配**（不是往旧 session 里塞东西）—— `MinimallToolProvider.provide(context)` 在构造期就把密码裹进 `pay_my_order` 的闭包了（`CharApp/minimall/provider.py:92-126`），旧 session 的闭包里没有它
- **`resume` 沿用挂起那次的 `run_id`** —— 否则恢复段的帧与成本会脱离账本（issue 33 就是为这条做的）

**跑分器不需要复制服务端的幂等键认领那一套**（`server/app.py` 第 4 步）：那是 HTTP 层的闸门（防双击、防重发、防断线重连），而跑分器一次只跑一条、自己知道只调一次。这条差异要写进 docstring —— 免得下次读的人以为漏了。

### 二、密码的来路与"零落盘"

- **从环境变量读**（照项目既有纪律：敏感配置一律走环境变量），变量名在实施时定（倾 `CHARAPP_EVAL_PAYMENT_PASSWORD`，加进 `.env.example` 的占位）
- **不落题面文件**（issue 42 的 YAML 里没有它）
- **不落报告**（报告里只出现"这次恢复了 / 恢复成功"这类事实，不出现值）
- **不落任何日志**：这条已经有现成的出口 —— `log_redaction.LOG_FIELDS` 里 `**.payment_password` 是 `WIPE`（`CharApp/minimall/log_redaction.py:62`），跑分器只要走同一个 `redacting_writer` 就自动打码

## 交付物

| # | 内容 |
|---|------|
| 1 | 跑分器的挂起检测 + 模拟确认流程（照上面那个形状） |
| 2 | 密码从环境变量的读取（含 `.env.example` 的占位行） |
| 3 | 用例：一条"下单"题自动跑完（挂起 → 确认 → 下成），且 `RunFacts.outcome` 是 `passed` 不是 `suspended` |
| 4 | 用例：一条"代付"题同上（它多一个载荷） |
| 5 | **否定断言**：密码不在题面 YAML、不在报告（JSON + Markdown）、不在任何落盘文件里 |
| 6 | 用例：**不模拟确认时**那几道题如实记成 `suspended`（别把"只有确认才能跑完"这件事藏起来） |

## 验收

- [x] 一条"下单"题自动跑完：挂起 → 确认 → 订单下成，`outcome = passed`
      （框架里的取值叫 `COMPLETED` —— `RunOutcome` 没有 `PASSED` 这一档；断言落在
      「终局是 `COMPLETED` + `POST orders/` 真被打到 + 那一条调用终于 `succeeded`」）
- [x] 一条"代付"题自动跑完：二次确认注入载荷 → 付款成功
      （题集里没有代付题，这一条用例自带一条 —— 见实施记录的出入 ①）
- [x] **密码不在题面文件、不在报告、不在任何落盘物里**（照 ADR-0015 那三条否定断言的写法：正对照是订单号）
      （四处：题面 YAML / 报告 JSON / 报告 Markdown / 落盘的这两个文件 / 记录层那三张行 —— 每一处都配了正对照）
- [x] 跑分器**不调** `service.aclose()`，或调了也不炸（见 issue 41 的 `dispose` 坑）
      （跑分器自己不调；`open_harness` 每一跑收尾调一次，issue 41 的 `dispose` 兜住了。
      本片另加了一条：`HarnessSubject.aclose()` 关模型，且再关一次不炸）
- [x] 不模拟确认的那一组如实在报告里显示 `suspended` 计数
      （`summarize_attempts` 上确实：挂起那条 `outcomes["suspended"] == 1` 且 `counted == 0`）

> **2026-09-28 补注（issue 41 已落地）**：恢复那条路要的两样都在跑分环境里 ——
> `harness.service`（`session_for` 在它上面，「重新装配一次」照 HTTP 那条路做）与
> `harness.records`（挂起检测走 `ToolCallsRepository(harness.records).list_pending_approvals(thread_id)`）。
> 本片验收里那句「跑分器不调 `service.aclose()`，或调了也不炸」已由 `open_harness` 的收尾覆盖
> （假库有了 `dispose`）。详情见 issue 41 的「给 issue 42 / 43 / 44 / 45 的话」。

> **2026-09-28 补注（issue 42 已落地）**：题集在 `CharApp/eval/cases/*.yaml`（20 条，六个场景，
> 校验与加载在 `CharApp/eval/golden.py`），判据在 `CharApp/eval/judges.py`（五个，`DEFAULT_JUDGES`）。
> **要照 `case.meta["buyer_id"]` 传给 `harness.context(..., user_id=)`** —— 只有 `order-04` 指了
> 那只贵车买家，不换人的话那一题会挂起（2598 < 5000）而挂起不进汇总。加题之后 `test_eval_golden.py`
> 里的 `CASE_COUNT` 也要跟着改。详见 issue 42 的「给 issue 43 / 44 / 45 的话」。

## 要定死的开放决策

| # | 决策 | 倾向 |
|---|------|------|
| 1 | "模拟确认"是跑分器的开关还是默认行为 | **默认行为**（题目跑得完才有分可判），但报告头部要写明"挂起题由跑分器模拟确认"—— 否则读者会以为模型自己走完了全流程 |
| 2 | 要不要模拟"买家点了拒绝" | **不做**。拒绝那条路（`Approval.reject`）已经被 issue 37 的取消面覆盖过，L4 没有要它回答的问题 |
| 3 | 恢复失败（如密码错）怎么记 | 记成一条判据失败（`reason` 写明"模拟确认失败"），**不是框架错误** —— 否则整批会中断 |
| 4 | 挂起检测放在哪一层 | 跑分器的 `EvalSubject.run_once` 里（业务侧），**不要**塞进框架的跑批器 —— 框架不该知道"挂起"这件事在业务里意味着什么 |

---

## 实施记录（2026-09-28）

五条开放决策全按倾向落地（第 3 条的**落点**改了，见出入 ②）；交付物 1 / 2 / 5 与票面
一致，3 / 4 / 6 的形状一致而**题**的来路不同（见出入 ①）。框架侧（`CharAgent/`）**一行
未动**。

### 一、落点

| # | 交付物 | 落在哪 |
|---|--------|--------|
| 1 | 挂起检测 + 模拟确认流程 | `CharApp/eval/subject.py`（新，374 行）：`HarnessSubject.run_once` 里三段 —— 问 → `_settle`（检测 + 确认）→ `_facts_of` |
| — | 检测那一半（记录层读回） | `CharApp/eval/harness.py` 的 `pending_approvals(thread_id)`（走仓储那一条判据） |
| 2 | 密码从环境变量读 | `CharApp/minimall/config.py` 的 `ENV_EVAL_PAYMENT_PASSWORD` + `eval_payment_password()`；占位加在根 `.env.example` |
| 3 | 用例：下单题自动跑完 | `CharApp/tests/test_eval_subject.py`（新，13 条） |
| 4 | 用例：代付题同上 | 同上（题是这一页自带的，见出入 ①） |
| 5 | 否定断言：密码不落任何落盘物 | 同上：题面 YAML / 报告 JSON / 报告 MD / 落盘的两个文件 / 记录层三张行，每处配正对照（订单号） |
| 6 | 用例：不模拟确认时如实记挂起 | 同上（连 `summarize_attempts` 的汇总一起看） |
| — | 顺带（票面没要，理由见出入 ⑤） | `RunFacts.config` 的模型 / 提示词 / 工具数 / 模拟确认；`RunFacts.cost` 从 `charagent_runs.total_cost` 读（`harness.cost_of`） |

### 二、七处与票面的出入

① **票面假设题集里有代付题，实际没有。** 票面第一段写「20 道题里凡"下单 / 代付"的都
会挂住」—— 但 `grep pay_my_order CharApp/eval/cases/` 是空的（下单那条有：`order-03`）。
于是代付那条用例**自带一条题**（`pay_case()`），题集不动。后果要如实记：**真跑那 20 题
永远碰不到代付那条恢复路**。要不要补题留给 44 / 45（补了会动 issue 42 那三条守着题数的
用例：`CASE_COUNT=20` / 每场景 3-4 条 / 恰好一条 `expect_suspend`）。

② **开放决策 3 的落点从「判据失败」改成 `BROKEN` + `RunFacts.error`。** 那句话自己后半截
给了理由 —— 「**不是框架错误**」，而判据失败会把它算进分母、变成一次「模型答错」。框架
对这类事本来就有专门一档（没跑成的三种如实计数、不进分子分母），报告上照样看得见（逐题
表 + `error` 那一列）。**整批不中断**这条两处等价。

③ **挂起检测那条路要多补三层筛选。** 票面 §一 直接写 `pending = await ToolCallsRepository
(database).list_pending_approvals(thread_id)` —— 在假库上会交出**全库**的调用行（issue 41
的坑 1：假库不过滤 `WHERE` 也不 join）。落在 `harness.pending_approvals`：状态 / 已批标志 /
会话归属三条自己补上，与真库那份 join 给出同一个集合。

④ **凭据只注入点名要它的那一调**（票面的形状是无条件 `{**context.payload, "payment_password":
_password()}`）。判据是那一条的 `approval_needs`：下单那条挂起 `needs=()`，于是密码不进它
的载荷。好处是与「缺凭据就报错」用同一个判据（报错看 `needs` 而注入不看，那才是真会出事
的不对称）。**代价如实记**：眼下没有观察口能证明「配了密码也不进下单那一跑的载荷」，
断言得到的是间接的（没配密码时下单照跑）。

⑤ **两处搭车实现**（票面没要求）：`RunFacts.config` 那三格与 `cost` 那一格。前者是报告头部
配置块的内容（`RunFacts.config` 明写「只有装配会话的那一方知道这一跑用了什么参数」），后者
是报告第 7 块「两类收益」的成本侧。两处都补了用例（配了 / 没配价目表各一条）。

⑥ **票面 §二 的日志那条假设不成立。** 票面写「跑分器只要走同一个 `redacting_writer` 就
自动打码」—— 跑分这条链上**一个 writer / logger 都没有**（`harness.session` 递进去的事件
出口是个丢弃器，`redacting_writer` 全仓只有 `server.py:432` 在用）。于是不是「打了码」而是
**压根没有日志**。这一句写进了 `subject.py` 的模块 docstring 与 `.env.example`：将来谁给
跑分入口加输出，那一个必须走 `redacting_writer`。

⑦ **票面交付物 3 的 `outcome = passed`**：`RunOutcome` 没有这一档，对应的是 `COMPLETED`，
断言按后者写（票面措辞松，不算偏差，记一笔免得后人去搜 `PASSED`）。

### 三、两轴复核后的修补（10 条，改 8 保留 2）

| 轴 | 发现 | 处置 |
|----|------|------|
| 标准 | `_cost_of` 在 subject 里翻 `records.runs`（Feature Envy：记录层那条线归跑分环境） | 移到 `harness.cost_of`，与 `calls_of` 并排 |
| 标准 | 「哪几条挂起点名要密码」写了两遍（报错一处、注入一处） | 提成模块级 `_wants_password` |
| 标准 | `_settle` 返回 `(LoopResult, str)` + `last is first` 判「有没有第二段」（隐式契约） | 改成返回 `(LoopResult \| None, str)`，`None` = 没有第二段 |
| 标准 | `18` 写死在用例里（与 `test_server.py` 重复，多一个工具要红两处） | 改读权威清单 `conftest.TOOL_NAMES` 的长度 |
| 标准 | `asking` / `asking_like_the_runner` 读起来像动词 | 改成 `subject_with` / `subject_from_factory` |
| 标准 | `config.eval_payment_password` 的「只写了空白」那一支没用例（别的 env 读取都有一块） | `test_server.py` 补一条（空 / 空白 / 带空格三态） |
| 规格 | **docstring 与代码不符**：模块里写「这个变量只在代付题上被读到」，而工厂建一次就无条件读 env —— 条件化的是**注入**不是读取 | 改成两句：读取全批一次、注入只看 `needs`；并写明那条区别眼下没有观察口 |
| 规格 | 开放决策 3 的偏离只在用例 docstring 里提了一句「这是决策 3 要的」，读起来像照做了 | 在 `subject.py` 模块 docstring 里直说「这一处与票面不同」+ 理由 |
| 标准 | `except Exception`（系统 CLAUDE.md §6.3 禁泛用） | **保留**：`runner.py` 在同一接缝上同样宽，且原处已写明为什么窄了会出事 |
| 标准 | 交付物 1 / 4 里两处刻意与票面同形（`resume_run` 那三处语义、题集不动） | **保留**：票面点名「照抄 HTTP 那条路」与「不要塞进框架」，而题集归 issue 42 |

### 四、测试

| 命令 | 结果 |
|------|------|
| `pytest`（CharApp 全量） | **295 passed**（43 开工时 280：+15 = 新文件 13 + harness 1 + server 1） |
| `pytest`（CharAgent 全量） | **1370 passed, 132 deselected**（本片一行未动，沿用开工前的绿） |
| `ruff check` + `format --check`（CharApp） | All checks passed |

本片**不跑真模型**：判据与事实都用手工剧本的假大脑造（MockLLM + respx 假商城，离线）。
真模型那条路是 44 / 45 第一次真跑时要过的第一关。

### 五、给 issue 44 / 45 / 46 的话

- **造组的入口是 `subject_factory(model_for, *, simulate_approval=True, payment_password=None)`**
  （`CharApp/eval` 门面里）。`model_for` 每一跑现调一次（模型所有权随那一跑交出去）；
  `payment_password=None` 会去读 `CHARAPP_EVAL_PAYMENT_PASSWORD`。
- **`open_harness(model)` 仍然只收一个模型** —— 裁剪钩子（44）与 prompt 版本（45）的注入
  口都还没开，两片的补注里已各自写明。
- **题集里没有一条会调 `pay_my_order`**（出入 ①）：要量代付那条恢复路就得加题，而加题会
  动 issue 42 的三条守着题数 / 分布 / 挂起条数的用例。
- **成本那一格现在是活的**（`RunFacts.cost` ← `charagent_runs.total_cost`）：配了
  `CHARAGENT_MODEL_PRICES` 就有数，没配是 `None`（不是 0）。46 收口时报告第 7 块的
  成本侧据此说得清「为什么这一格是空的」。
