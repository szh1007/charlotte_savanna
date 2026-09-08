# 27-P2-3 — memory 插件（四层记忆 + 租户隔离 + 遗忘机制）

**What to build:** 记忆系统插件（P2）：四层记忆（短期/情景/语义/程序性），冲突用时间衰减（RecencyWeighting）解决；多租户隔离（tenant_id/user_id 检索强制过滤防跨租户泄露 #32）；写入/检索/遗忘机制（软删除标记 #33）。memories 表 P2 追加到 alembic；经 hook（before_turn 注入 / after_turn 写入）挂载（ADR-0007）。

**Blocked by:** 05, 08

**Status:** ready-for-agent

- [ ] 四层记忆实现 + 时间衰减权重（#31）
- [ ] memories 表迁移 + 写入/检索（#32）
- [ ] 多租户隔离：跨租户检索零结果测试（#32）
- [ ] 遗忘机制：软删除（#33）
- [ ] 插件经配置注册挂载，核心零 import（ADR-0007）
