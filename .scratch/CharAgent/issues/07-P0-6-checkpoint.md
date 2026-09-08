# 07-P0-6 — Checkpoint 序列化 + 三实现 + time-travel

**What to build:** CheckpointSaver 协议 + 序列化协议（JSON 主格式 + 自定义 encoder/decoder 处理 datetime/嵌套 dict + schema_version 向前兼容迁移），InMemory / Redis（KV 快照 + TTL）/ Postgres（强一致 + 历史）三实现配置切换（ADR-0002）。每 Turn 结束落盘（含 HITL 挂起点），支持断点续跑（不重复已完成动作）与 time-travel 回溯（恢复历史 checkpoint → parent_id 新分支）。

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] 序列化协议：JSON + 自定义序列化器 + schema_version；版本升级走迁移函数，旧快照可读回（#5）
- [ ] InMemory / Redis / Postgres 三实现经同一协议可配置切换（ADR-0002）
- [ ] 断点续跑：快照后中断 → 从 checkpoint 恢复 → 不重复执行已完成动作（#5）
- [ ] time-travel：恢复历史时刻 → parent_id 产生新分支
- [ ] 双实现语义对比测试：同一事件序列喂 Redis / Postgres，恢复结果一致；Postgres 历史可回溯（ADR-0002 验收）
- [ ] 挂起点快照：HITL 挂起也落快照（为 #25 恢复而非重跑打底）
