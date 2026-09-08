# 09-P0-8 — MockLLM 三模式 + 轨迹断言 + 快照测试基建

**What to build:** 测试基建：MockLLM 实现 ChatModel 协议三模式（固定返回 / 脚本化序列 / 录制回放），被测代码零改动（#61）；确定性保证（temperature=0 + seed + 注入随机源）；轨迹断言工具（断言工具调用顺序与参数而非仅最终答案 #62）；快照/契约测试（checkpoint 序列化、事件流、双适配器契约 #63）。测试矩阵落地：并行工具、错误自纠错、循环防护、断点续跑、reasoning、HITL、幂等、取消、降级、限流边界等核心场景。

**Blocked by:** 01, 04, 07

**Status:** ready-for-agent

- [ ] MockLLM 三模式完成，被测代码零改动（#61）
- [ ] 确定性：temperature=0 + seed + 注入随机源，同输入同输出（#61）
- [ ] 轨迹断言工具：工具调用顺序与参数断言（#62）
- [ ] 快照测试：checkpoint 序列化 JSON 快照、SSE 事件序列快照、双适配器契约（#63）
- [ ] 测试矩阵核心场景全部落地（并行/纠错/防护/续跑/reasoning/HITL/幂等/取消/降级/限流）
- [ ] `pytest tests/` 全绿（全 mock，CI 默认）
