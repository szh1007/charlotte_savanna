# 21-P1-11 — Token 计量

**What to build:** TokenMetering：请求前预估（tiktoken/BPE 原理，不同模型 tokenizer 差异）做预算截断与成本核算；响应后 usage 回填（双适配器均解析 usage）；token 预算与 LoopGuard 挂钩（预算超限提前截断）（#35）。

**Blocked by:** 01, 04

**Status:** ready-for-agent

- [ ] 请求前 token 预估（含 tool schema 与历史窗口）（#35）
- [ ] usage 回填：双适配器解析 usage 一致
- [ ] 预算挂钩：预估超预算提前截断；LoopGuard 读 token 预算（#35）
- [ ] 计量结果进入 run 记录（total_tokens 字段）
