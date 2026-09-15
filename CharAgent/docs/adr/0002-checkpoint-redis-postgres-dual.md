# ADR-0002: Checkpoint 用 Redis + Postgres 双实现（跳过 SQLite）

- 状态: accepted
- 日期: 2026-08-18（自 DESIGN.md 选型 0002 拆出；补充确认：本机 Postgres 与 Redis 均已运行）
- 考虑过的方案: SQLite——拒绝，无法体现并发与事务语义差异；仅 InMemory——拒绝，无法演示生产级持久化
- 后果: 需维护 Redis/Postgres 连接管理；本地运行需 Redis + Postgres 服务

`CheckpointSaver` 协议提供 InMemory（测试）、Redis、Postgres 三种实现，运行时配置切换。Redis（KV 快照，快但弱一致）与 Postgres（关系型、有事务、支持历史 / time-travel）代表两种存储语义，双实现演示「存储介质不同，checkpoint 语义也不同」。

## 更新（2026-09-13，P0-6 交付后按需调整）

**Redis 从「只留最新一帧」升级为「Stream 流式全历史」，并保留只留最新作为可选模式。**
理由：单纯「只留最新」只够断点续跑，翻历史 / 回溯分叉点 / 回放调试（issue 07 的交付目标）都做不到；
而这几件事在 LangGraph 里是协议层要求（`BaseCheckpointSaver.list()` 人人得实现）。

- `mode="history"`（默认）：一个会话一条 Stream（`XADD` 追加 / `XRANGE` 翻历史 / `MAXLEN` 裁剪 / `EXPIRE` 过期）
- `mode="latest"`：只留最新一帧（对应官方 langgraph-checkpoint-redis 的 ShallowRedisSaver；
  短会话只需要续跑时的快而省选项，能力差异仍由 `capabilities` 显式声明）
- 配置：`CHARAGENT_CHECKPOINT_REDIS_MODE` / `CHARAGENT_CHECKPOINT_REDIS_MAX_FRAMES`

于是两种介质如今的差异不在「有没有历史」，而在**一致性、查询能力、保留策略与过期语义**：
Redis 快、可裁剪可过期、按会话分区（按编号取帧要带会话号）；Postgres 强一致、可建索引查询、留有全部历史。
