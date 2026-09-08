# 11-P1-1 — FastAPI server 骨架（REST + SSE + TaskQueue + healthz）

**What to build:** FastAPI 服务：全部 REST 端点（创建会话 / 拉历史 / 发起 run（长任务 HTTP 立即返回）/ SSE 事件流（after_event_id 断点续拉）/ 取消 / 审批列表 / 审批通过/拒绝 / 转人工 / 人工回复 / 工单列表 / 健康检查）；SSE 事件协议（四类事件 + approval_required + reasoning + final + error，每事件带 seq）；TaskQueue 进程内 asyncio FIFO + 并发状态锁（ADR-0006 抽象预留分布式 MQ）；/healthz 依赖探活（PG/Redis/Milvus/MySQL）；所有 POST 接受 request_id 幂等键。

**Blocked by:** 10

**Status:** ready-for-agent

- [ ] 全部 REST 端点可调，返回结构符合 API 协议（#20/#12）
- [ ] SSE 事件流端到端：run 执行中事件实时推送；after_event_id 断点续拉（#4）
- [ ] TaskQueue：并发请求排队、长任务 HTTP 立即返回 + 后台执行（#20）
- [ ] /healthz：PG/Redis/Milvus/MySQL 依赖探活（#64）
- [ ] POST 幂等：同 request_id 重复提交返回已有结果（#13/#17）
- [ ] E2E 测试：REST → TaskQueue → loop → 工具 → SSE 事件序列断言（mock LLM + 打桩外部服务）
