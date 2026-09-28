# 43 · 业务侧：挂起题的模拟确认（跑分器自动走完下单与代付）

**Status:** ready-for-agent

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

- [ ] 一条"下单"题自动跑完：挂起 → 确认 → 订单下成，`outcome = passed`
- [ ] 一条"代付"题自动跑完：二次确认注入载荷 → 付款成功
- [ ] **密码不在题面文件、不在报告、不在任何落盘物里**（照 ADR-0015 那三条否定断言的写法：正对照是订单号）
- [ ] 跑分器**不调** `service.aclose()`，或调了也不炸（见 issue 41 的 `dispose` 坑）
- [ ] 不模拟确认的那一组如实在报告里显示 `suspended` 计数

> **2026-09-28 补注（issue 41 已落地）**：恢复那条路要的两样都在跑分环境里 ——
> `harness.service`（`session_for` 在它上面，「重新装配一次」照 HTTP 那条路做）与
> `harness.records`（挂起检测走 `ToolCallsRepository(harness.records).list_pending_approvals(thread_id)`）。
> 本片验收里那句「跑分器不调 `service.aclose()`，或调了也不炸」已由 `open_harness` 的收尾覆盖
> （假库有了 `dispose`）。详情见 issue 41 的「给 issue 42 / 43 / 44 / 45 的话」。

## 要定死的开放决策

| # | 决策 | 倾向 |
|---|------|------|
| 1 | "模拟确认"是跑分器的开关还是默认行为 | **默认行为**（题目跑得完才有分可判），但报告头部要写明"挂起题由跑分器模拟确认"—— 否则读者会以为模型自己走完了全流程 |
| 2 | 要不要模拟"买家点了拒绝" | **不做**。拒绝那条路（`Approval.reject`）已经被 issue 37 的取消面覆盖过，L4 没有要它回答的问题 |
| 3 | 恢复失败（如密码错）怎么记 | 记成一条判据失败（`reason` 写明"模拟确认失败"），**不是框架错误** —— 否则整批会中断 |
| 4 | 挂起检测放在哪一层 | 跑分器的 `EvalSubject.run_once` 里（业务侧），**不要**塞进框架的跑批器 —— 框架不该知道"挂起"这件事在业务里意味着什么 |
