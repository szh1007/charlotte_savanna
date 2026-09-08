# 23-P1-13 — 客服 demo 业务层（工具集 + 表 + 转人工）

**What to build:** 电商售后客服 demo：业务工具集（订单查询（只读 MySQL，独立查询层不依赖 Django ORM）、物流追踪、退款审批（HITL 挂起）、FAQ、知识库检索、转人工）；demo 业务表落 Postgres（tickets / escalations / approvals / audit_logs，与 checkpoint 同库同 alembic 体系）；转人工接管流程（escalate → ticket + escalation → 人工接管查看历史 → reply 写 assistant 消息）（#19/#25）；退款工具挂幂等键防重复执行（#17）。

**Blocked by:** 11, 14, 16, 17, 19

**Status:** ready-for-agent

- [ ] 业务工具集全部注册可用（订单只读查询 / 物流 / 退款审批 / FAQ / 知识库检索 / 转人工）（#19/#25）
- [ ] 订单查询：只读账号 + 独立查询层，不 import Django ORM（ADR-0004/#24）
- [ ] Ticket/Escalation/Approval 表迁移落地（与 checkpoint 同库）
- [ ] 转人工流程端到端：用户请求 → ticket+escalation → 人工接管（历史可见）→ 回复（#19）
- [ ] 退款审批：HITL 挂起 → 审批 → 恢复/拒绝/超时全路径（#25）
- [ ] 退款幂等：同 request_id 不重复执行退款副作用（#17）
