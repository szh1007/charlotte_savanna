# 04 — 网关补齐：限流 + 双粒度审计

**What to build:** 在 01 的最小网关上补两项能力。**限流**：per user / per tool 两级（框架 `RateLimiter` 提供固定窗口 / 滑动窗口 / 令牌桶 / 漏桶实现，调用方是网关而非 agent 进程内），超限 429 + 排队提示，与 **CharAgent P1-5** 的框架侧实现对接。**双粒度审计**：工具调用粒度（谁 / 代表谁 / 工具名 / 参数摘要脱敏 / 耗时 / 结果状态，由工具层写）+ 数据访问粒度（实际访问的业务资源 ID / 命中的敏感字段：手机号 / 地址 / 余额 / 放行决定，由网关写），落 Postgres `audit_logs`。关键性质：因为网关是唯一入口，**这两项策略物理上不可绕过**——工具层代码里没有 minimall 的地址与凭证。前置脱敏：写审计前 PII 已脱敏（与 CharAgent P1-7 的脱敏组件复用）。

**表归属**：`charservice_audit_logs` 属 **CharService 自有迁移链**（独立版本表 `charservice_alembic_version`）——与 checkpoint 同库但**不同迁移链**，业务表定义不进 `CharAgent/alembic/versions/`。落点与理由见 PRD §4.7。

**Blocked by:** 03、**CharAgent P1-5**（`RateLimiter`）、**CharAgent P1-7**（脱敏组件）

**Status:** ready-for-agent

- [ ] 限流 per user / per tool 生效，超限返回 429 + 排队提示
- [ ] 审计落 `audit_logs`，双粒度两条流可分别查询
- [ ] 数据访问审计标记敏感字段命中（订单号 / 手机号 / 地址 / 余额）
- [ ] 审计写入前 PII 已脱敏（日志与审计共用脱敏组件）
- [ ] 能回答「客服 X 在什么时候看了用户 Y 的哪些订单、哪些字段」
- [ ] 绕过用例：直接构造请求打 minimall → 无地址无凭证，物理不可达
- [ ] 限流边界用例：per user 超限 / per tool 超限 / 恢复后放行
