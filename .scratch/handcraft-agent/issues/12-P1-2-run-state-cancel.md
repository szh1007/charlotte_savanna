# 12-P1-2 — run 状态机 + 流式中断/取消

**What to build:** RunState 状态机（created/running/waiting_tool/waiting_user/retrying/failed/finished/cancelled）由执行层推进而非模型建议；流式中断/取消（asyncio.Task.cancel 语义 + 工具协程取消 + 连接释放 + 幂等键留痕）：用户取消或断连时优雅终止，已执行真实副作用走幂等记录（未完成动作无副作用），run 状态置 cancelled，SSE 发 error(cancelled) 收尾。

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] RunState 状态机：合法迁移 + 非法迁移拒绝，状态持久化到 run 记录（#16）
- [ ] 取消：POST cancel → 工具协程取消、资源释放、run 状态 cancelled（#18）
- [ ] 断连处理：客户端断连后任务终止并留痕（#16）
- [ ] 已执行副作用：幂等键记录，不走补偿（#18 语义）
- [ ] 测试：取消后协程确实释放（无泄漏）、状态正确（#18）
