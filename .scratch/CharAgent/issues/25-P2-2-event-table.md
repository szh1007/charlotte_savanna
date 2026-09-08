# 25-P2-2 — event 表 + TTL 清理（事件溯源）

**What to build:** events 表（事件溯源 #12）：每次状态变化以不可变事件追加（event_type / run_id / payload / seq），支撑 trace 回放（#59）与审计；数据模型补全：会话过期清理、checkpoint 历史保留策略（TTL）。追加到 alembic 迁移体系，不回溯改造 P0/P1 表。

**Blocked by:** 08

**Status:** ready-for-agent

- [ ] events 表迁移 + 写入链路：状态变化 → 不可变事件追加（#12）
- [ ] 事件可回放：按 run_id/seq 重放重建状态（支撑 #59 trace 回放）
- [ ] TTL 清理：会话过期、checkpoint 历史保留策略（#12）
- [ ] 迁移体系追加，P0/P1 表不动
