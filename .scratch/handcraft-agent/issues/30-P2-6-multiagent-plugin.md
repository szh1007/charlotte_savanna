# 30-P2-6 — multiagent 插件（Supervisor / P2P / Critic / handoff）

**What to build:** 多 agent 插件（P2）：Supervisor 拓扑（主 Agent 调度 Subagent）与 P2P 对等拓扑；Blackboard 共享黑板协作（与私有记忆相对）；Critic 评审 agent（对主 agent 输出独立评审/挑错）；handoff 移交 + 循环检测（#42-44）。基于核心 loop 与 hook 挂载（ADR-0007）。

**Blocked by:** 04

**Status:** ready-for-agent

- [ ] Supervisor 拓扑：主 Agent 调度 Subagent 完成子任务（#42）
- [ ] P2P 拓扑 + Blackboard 共享状态（#42/#43）
- [ ] Critic：独立评审/挑错回路（#43）
- [ ] Handoff + 循环检测（#44）
- [ ] 插件经配置注册挂载，核心零 import（ADR-0007）
