# 11-P1-1 — FastAPI server 骨架（REST + SSE + TaskQueue + healthz）

> **2026-09-18 修订**（ADR-0008 决策 9 分层剥离）：本 issue **拆为两半**。
> - **留 CharAgent**：通用运行时端点（创建会话 / 拉历史 / 发起 run / SSE 事件流 / 取消 / 健康检查），以 **router 工厂**（`create_runtime_router(deps)`）交付而非独立服务——框架是库，应用自行组装
> - **去 CharService**：业务端点（审批列表 / 审批通过拒绝 / 转人工 / 人工回复 / 工单列表）——它们是客服业务，见 `.scratch/CharService/issues/01`、`07`、`09`
>
> 另新增一项框架侧要求：**会话创建必须支持绑定身份**（`user_id` / `tenant_id`）并交由应用注入委托凭据，身份链路的落地见 ADR-0009。

**What to build:** FastAPI 运行时层（router 工厂形式）：通用 REST 端点（创建会话 / 拉历史 / 发起 run（长任务 HTTP 立即返回）/ SSE 事件流（after_event_id 断点续拉）/ 取消 / 健康检查 / **列挂起 run** / **恢复挂起 run**）；会话创建支持绑定 `user_id` / `tenant_id`，并接受应用通过 `ToolContext` 注入的凭据（通道由 P1-15 提供）；SSE 事件协议（四类事件 + approval_required + confirmation_required + reasoning + final + error，每事件带 seq）；TaskQueue 进程内 asyncio FIFO + 并发状态锁（ADR-0006 抽象预留分布式 MQ）；/healthz 依赖探活（PG/Redis/Milvus/MySQL）；所有 POST 接受 request_id 幂等键。

**新增的两个控制面端点**（ADR-0010 决策三）——挂起 run 的出口，与 `cancel` 同层：

```
GET  /runs?status=<等待态>   → [{run_id, thread_id, suspension:{kind,reason,payload}}]
POST /runs/{run_id}/resume   → load_latest(thread_id) → loop.resume(checkpoint)
```

`resume` **不需要原 loop 实例**（P0 的 resume 从 checkpoint 起步，可用新构造的 loop）。

**Blocked by:** 10, 35

**Status:** ready-for-agent

- [ ] 通用运行时端点全部可调，返回结构符合 API 协议（#20/#12）；以 router 工厂交付，可被应用组装
- [ ] 会话创建可绑定身份（`user_id` / `tenant_id`）；凭据经 `ToolContext` 注入（通道见 P1-15）
- [ ] **控制面端点**：`GET /runs?status=<等待态>` 返回挂起 run 及 `suspension` 载荷；`POST /runs/{run_id}/resume` 从挂起 checkpoint 恢复执行（**不重跑已完成动作**）
- [ ] 控制面用例：挂起 → 列表能查到 → resume → 断言恢复而非重跑；`resume` 在**原 loop 实例已不存在**时也能工作
- [ ] **会话状态门控**：非 `ACTIVE` 会话拒绝发起 run（`ThreadStatus` 有 `CLOSED` / `ESCALATED` 两态，但 P0 只定义了取值、没定义行为 —— 应用侧「人工接管期间 agent 不再自动回复」要靠这道门控兜底）
- [ ] SSE 事件协议含 `confirmation_required`（用户确认挂起，与 `approval_required` 内部审批挂起区分）
- [ ] SSE 事件流端到端：run 执行中事件实时推送；after_event_id 断点续拉（#4）
- [ ] TaskQueue：并发请求排队、长任务 HTTP 立即返回 + 后台执行（#20）
- [ ] /healthz：PG/Redis/Milvus/MySQL 依赖探活（#64）
- [ ] POST 幂等：同 request_id 重复提交返回已有结果（#13/#17）
- [ ] E2E 测试：REST → TaskQueue → loop → 工具 → SSE 事件序列断言（mock LLM + 打桩外部服务）
- [ ] **`conversation_turns` 端到端验证**（P0 预埋路径，零真实调用方）：`GET /threads/{id}/messages` 接线后跑两轮 → 查库 → 断言会话里**恰好两条问答、无重复行**。第一次要面对的问题是 `prior_len` 从哪来、算得准不准：传对只收新增段；传 0 则老消息被重写一遍，且切片里若有多个 `user`，前面几问会落库成 `answer=None`（`TurnPair` 的边界说明）
