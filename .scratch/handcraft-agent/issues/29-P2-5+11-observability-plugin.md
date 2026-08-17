# 29-P2-5+11 — observability 插件（Langfuse/Grafana/Loki + 数据删除权）

**What to build:** 可观测补全插件（P2）：Langfuse trace（compose 自托管，经 on_event hook 采集 #41）；指标告警（Grafana）；版本化（prompt/model/tool 版本随 trace 记录，支持灰度与回滚 #40）；Loki 日志聚合（#39）。数据合规删除权（GDPR 被遗忘权）：用户要求删除时物理删除并级联到衍生数据（向量/checkpoint/trace），而非仅软删除（#28）。经事件总线 + hook 挂载（ADR-0007）。

**Blocked by:** 05, 22, 25

**Status:** ready-for-agent

- [ ] Langfuse trace 对接（on_event 采集，compose 自托管）（#41）
- [ ] 指标告警（Grafana）+ 日志聚合（Loki）（#39）
- [ ] 版本化：prompt/model/tool 版本随 trace 记录（#40）
- [ ] 数据删除权：级联删除向量/checkpoint/trace（#28）
- [ ] 插件经配置注册挂载，核心零 import（ADR-0007）
