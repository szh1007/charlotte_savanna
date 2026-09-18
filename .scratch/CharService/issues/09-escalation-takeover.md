# 09 — 转人工 + 接管台

**What to build:** `escalate_to_human(reason)` 工具 + Ticket / Escalation 落 Postgres（**与 checkpoint 同库、但属 CharService 自有迁移链**，表名 `charservice_` 前缀，不走框架五实体表）+ 人工接管流程：用户请求 → 建 Ticket + Escalation → 人工客服在接管台查看**完整会话历史（含工具调用轨迹）** → 回复（`POST reply` 写 assistant 消息，`run_id` 为 NULL 表示非 agent 产生）→ 用户继续对话。`ThreadStatus.ESCALATED` 状态在框架侧 P0 已定义，此处接通。同时覆盖降级场景的转人工建议：LLM_DOWN / RAG_DOWN / **`DEPENDENCY_DOWN`** / 审批超时 都应能导向转人工。

**完整轨迹从哪来**（2026-09-18 补）：框架的 `GET /threads/{id}/messages` **刻意只返回一问一答**（工具消息被 `hidden` 过滤），接不了这份需求。框架为此提供**管理端轨迹端点** `GET /runs/{run_id}/transcript`（CharAgent P1-1），接管台改调它。

**Blocked by:** 08、**CharAgent P1-1**（`transcript` 端点）

**Status:** ready-for-agent

- [ ] `escalate_to_human` 工具可用，创建 Ticket + Escalation
- [ ] 接管台可查看转人工会话的完整历史（含 thinking / tool_call / tool_result 轨迹）
- [ ] `POST reply` 写 assistant 消息，`run_id` 为 NULL（与 agent 消息可区分）
- [ ] 会话状态置 `escalated`，人工接管期间 agent 不再自动回复
- [ ] 降级路径汇入转人工：LLM_DOWN / RAG_DOWN / 网关不可用 / 审批超时
- [ ] 端到端：用户提问 → 转人工 → 人工接管 → 回复 → 用户看到回复
- [ ] Ticket / Escalation 迁移落地，与 checkpoint 同库不冲突
