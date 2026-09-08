# ADR-0002: Checkpoint 用 Redis + Postgres 双实现（跳过 SQLite）

- 状态: accepted
- 日期: 2026-08-18（自 README 选型 0002 拆出；补充确认：本机 Postgres 与 Redis 均已运行）
- 考虑过的方案: SQLite——拒绝，无法体现并发与事务语义差异；仅 InMemory——拒绝，无法演示生产级持久化
- 后果: 需维护 Redis/Postgres 连接管理；本地运行需 Redis + Postgres 服务

`CheckpointSaver` 协议提供 InMemory（测试）、Redis、Postgres 三种实现，运行时配置切换。Redis（KV 快照，快但弱一致）与 Postgres（关系型、有事务、支持历史 / time-travel）代表两种存储语义，双实现演示「存储介质不同，checkpoint 语义也不同」。
