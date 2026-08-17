# ⑩ 架构与策略（#55-57）

> 返回 [难点清单索引](../README.md#难点清单)

| ID | 难点 | 详细细节 | 阶段 |
|----|------|----------|------|
| 55 | Planning 范式 | • ReAct：边推理边行动（think → act → observe 循环） | P2 |
| | | | • Plan-and-Execute：先整体规划出步骤，再逐步执行 | |
| | | | • Reflection：执行后自我反思，发现不足再改进 | |
| | | | • Replan：执行中发现计划有问题，重新规划 | |
| 56 | Workflow vs Agent | • workflow 适用：路径确定、可控、便宜的固定步骤编排 | P2 |
| | | | • agent 适用：路径不确定、需要模型动态决策的场景 | |
| | | | • 实践原则：用 workflow 做骨架兜底，局部不确定环节才用 agent | |
| 57 | Agent 框架对比 | • LangChain / LangGraph / DeepAgents / 手写 各自的 tradeoff（权衡取舍） | P2 |
| | | | • 何时用框架、何时自研：核心诉求是控制力还是开发速度 | |
| | | | • LangGraph 的图模型 vs 手写 while 循环的差异与适用场景 | |
| | | | • 面试表达：能讲清「框架替你做了什么、为什么那么设计」是理解深度的分水岭 | |
