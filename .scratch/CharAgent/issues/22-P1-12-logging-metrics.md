# 22-P1-12 — 结构化日志 + Prometheus 指标

**What to build:** StructuredLogging：structlog JSON 格式 + request_id/trace_id/thread_id 贯穿（全请求链路），脱敏前置（日志写入前 PII 已脱敏）（#38）；指标最小集：Prometheus 文本格式（success/retry/escalation rate、p95 latency、工具成功率、guardrail block rate）（#39）。observability 插件（Langfuse/Loki）属 P2，此处只做核心层配置与本地输出。

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] structlog JSON 日志：request_id/trace_id/thread_id 贯穿，脱敏前置（#38）
- [ ] 指标最小集：Prometheus 文本格式导出（success/retry/escalation rate、p95 latency、工具成功率、guardrail block rate）（#39）
- [ ] 日志级别与上下文规范（DEBUG→INFO→WARNING→ERROR，含关键上下文不含敏感信息）
- [ ] 测试：脱敏前置验证（日志无 PII）
