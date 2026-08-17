# 15-P1-5 — 限流（固定/滑动窗口 + 令牌桶/漏桶）

**What to build:** RateLimiter 限流器：固定窗口 / 滑动窗口 / 令牌桶 / 漏桶四种算法，per user / tenant / model 多级限流（#22/#68 横切）；超限返回 429 + 排队提示；与模型预算保护衔接（限流兜底模型侧）。

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] 四种算法实现 + 边界测试（窗口边界、突发流量、桶耗尽）（#22）
- [ ] 多级限流：per user / tenant / model 独立配额
- [ ] 超限行为：429 + 重试提示，按错误码协议（RATE_LIMITED）
- [ ] 注入随机源保证限流测试确定性（#61）
