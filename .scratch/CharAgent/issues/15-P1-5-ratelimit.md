# 15-P1-5 — 限流（固定/滑动窗口 + 令牌桶/漏桶）

**What to build:** RateLimiter 限流器：固定窗口 / 滑动窗口 / 令牌桶 / 漏桶四种算法，per user / tenant / model 多级限流（#22/#68 横切）；超限返回 429 + 排队提示；与模型预算保护衔接（限流兜底模型侧）。

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] 四种算法实现 + 边界测试（窗口边界、突发流量、桶耗尽）（#22）
- [ ] 多级限流：per user / tenant / model 独立配额
- [ ] 超限行为：429 + 重试提示，按错误码协议（RATE_LIMITED）
- [ ] 注入随机源保证限流测试确定性（#61）
- [ ] **调用方是网关**（2026-09-18 修订）：框架交付 `RateLimiter` 实现，网关（独立进程）调用它；agent 进程内不再是限流点
- [ ] **per tool 维度**：除 per user / tenant / model 外新增 per tool 配额（防单个昂贵工具被滥用）

---

**2026-09-18 修订**（ADR-0008）：**部署位置改变**——限流点从 agent 进程内移到**网关上**。理由是「可绕过性」：限流若在 agent 侧，写个新 client 就绕过了；网关是访问业务系统的唯一入口，策略在那里才真正生效。框架侧交付物不变（四种算法的 `RateLimiter` 实现），只是调用方变成网关。业务侧落点见 `.scratch/CharService/issues/04`。
