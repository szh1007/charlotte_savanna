# 04-P0-3 — agent loop 核心（并行工具 / 错误自纠错 / 循环防护 / length 截断）

**What to build:** 手写 agent loop（while 循环：模型决策 → 并行工具执行 → tool_result 消息回填 → 下一轮）。一个 assistant 消息的多个 tool_call 并发执行（gather + return_exceptions），结果以 tool_result 消息整体回填保持并行语义；可操作错误回填模型二次调用实现自纠错；LoopGuard（max_turns / token 预算 / wall-clock + kill switch 即时打断）防无限循环；finish_reason=length 走截断处理（续写或精简）。每 Turn 结束产生完整消息历史（供 checkpoint 落盘）。

**Blocked by:** 01, 03

**Status:** ready-for-agent

- [ ] 单工具调用路径：调用 → 回填 → 模型二次决策正确（#1）
- [ ] 并行工具：同一 assistant 消息多 tool_call 并发执行（时间戳验证非串行）；部分失败时成功结果与失败原因一起回填（#1）
- [ ] 错误自纠错：工具报可操作错误 → 回填 → 模型二次调用成功（#2）
- [ ] 循环防护：max_turns / token 预算 / wall-clock 三种触发点；kill switch 即时打断（#3）
- [ ] length 截断处理：续写或精简路径（#10）
- [ ] 轨迹断言测试：工具调用顺序与参数可断言（#62）
