# 12-P1-2 — run 状态机 + 流式中断/取消

**What to build:** RunState 状态机（created/running/waiting_tool/waiting_user/retrying/failed/finished/cancelled）由执行层推进而非模型建议；流式中断/取消（asyncio.Task.cancel 语义 + 工具协程取消 + 连接释放 + 幂等键留痕）：用户取消或断连时优雅终止，已执行真实副作用走幂等记录（未完成动作无副作用），run 状态置 cancelled，SSE 发 error(cancelled) 收尾。

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] RunState 状态机：合法迁移 + 非法迁移拒绝，状态持久化到 run 记录（#16）
- [ ] 取消：POST cancel → 工具协程取消、资源释放、run 状态 cancelled（#18）
- [ ] 断连处理：客户端断连后任务终止并留痕（#16）
- [ ] 已执行副作用：幂等键记录，不走补偿（#18 语义）
- [ ] 测试：取消后协程确实释放（无泄漏）、状态正确（#18）
- [ ] **挂起类型区分**：内部审批挂起与用户确认挂起是两个不同的 waiting 态，状态机需分别可查（**新态的落地在 P1-15**，本 issue 接的是迁移规则与推进者）

---

**2026-09-18 修订**（ADR-0009 + ADR-0010）：新增两种挂起语义的区分——「内部审批」与「用户确认」。两者在状态机上是**独立的 waiting 态**，不得合并成一个（放行者与超时策略都不同）。

- 原 8 态只有 `waiting_user`（等终端用户），**缺「等待内部审批」态** —— 由 **P1-15** 补态并定迁移规则；本 issue 负责**谁推进状态**：`Suspension` 由工具返回、loop 检测后置等待态（P1-15 的通道），执行层推进而非模型建议
- 业务侧落点见 `.scratch/CharService/issues/07`、`08`
