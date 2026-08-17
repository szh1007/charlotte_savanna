# ⑦ 多 agent（#42-44）

> 返回 [难点清单索引](../README.md#难点清单)

| ID | 难点 | 详细细节 | 阶段 |
|----|------|----------|------|
| 42 | 多 agent 拓扑与状态共享 | • 拓扑选择：Supervisor（一个主 agent 调度多个子 agent）vs P2P（agent 之间对等协商） | P2 |
| | | | • 共享黑板 vs 私有记忆：多 agent 通过共享状态协作，还是各自维护私有记忆 | |
| 43 | 多 agent 协作与纠错 | • Critic 模式：一个 agent 独立评审另一个 agent 的输出，互相纠错 | P2 |
| | | | • subagent 隔离：子 agent 失败不能拖垮主 agent | |
| 44 | 多 agent 控制流风险 | • handoff 循环检测：A 转给 B、B 又转回 A，要检测并打破这种循环 | P2 |
| | | | • 死锁预防：多个 agent 互相等待对方结果导致死锁 | |
