# 06-P0-5 — 重试 + 指数退避 + jitter + 幂等键

**What to build:** 重试策略：只对瞬态错误（429/5xx/超时）做指数退避 + jitter 重试，4xx 永久失败直接放弃；重试会重复计费 token（#13），需缓存响应或挂钩预算。幂等键（IdempotencyKey）生成与校验基础，为 P1 真实副作用防重复执行打底。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] RetryPolicy：指数退避 + jitter；瞬态错误重试、4xx 放弃（#13）
- [ ] 重试上限与总耗时上限可控
- [ ] 幂等键：生成 + 校验基础实现（防重复执行，P1 挂载到真实动作）
- [ ] 边界测试：退避序列、jitter 随机性注入（#61 确定性）
