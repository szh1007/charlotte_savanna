# 08-P0-7 — 五实体数据模型 + alembic 初始化

**What to build:** 五核心实体（Thread / Run / Message / ToolCall / Checkpoint）数据模型定义，P0 一次定死（含 schema 版本号向前兼容），Postgres 表 + alembic 首次迁移初始化。字段与协议定型一致：Run 状态机（created/running/waiting_tool/waiting_user/retrying/failed/finished/cancelled）、Message.reasoning 存而不回填历史、ToolCall.status 含 needs_approval（HITL）、Checkpoint.parent_id 分支来源。demo 表（tickets/escalations/approvals/audit_logs）与幂等表预留为 P1 迁移。

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] 五实体模型定义完成，字段与协议定型一致（#5/#12 实体部分）
- [ ] Postgres DDL 生成（alembic），首次迁移在空库可执行
- [ ] 实体字段与状态机枚举一致（Run/ToolCall status 全部取值）
- [ ] 迁移体系预留 P1 demo 表与 P2 event 表追加路径（#12）
