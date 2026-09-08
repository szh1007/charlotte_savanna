# 32-P2-8 — eval 插件（GoldenSet + LLM-judge + 数据飞轮）

**What to build:** Agent 评估体系插件（P2）：评估集（GoldenSet：问题-期望答案/期望工具调用）+ LLM-as-judge 打分（#58），缓解 judge 偏差（位置/长度/自我偏好：多 judge 交叉、交换顺序、结构化 rubric #60）；trace 回放调试（#59，经 on_event 采集 + event 表回放）；RAG 检索评估（hit rate / MRR / faithfulness，三层排查：检索不到/检索错/生成错 #48/#49）；数据飞轮（badcase 收集 → 归类 → 改进 → 回归闭环 #59）。

**Blocked by:** 05, 09

**Status:** ready-for-agent

- [ ] GoldenSet + LLM-judge 评估跑分（#58）
- [ ] judge 偏差缓解（多 judge/交换顺序/rubric）（#60）
- [ ] trace 回放调试（#59 + #25 event 表）
- [ ] RAG 检索评估（hit rate / MRR / faithfulness）（#48/#49）
- [ ] 数据飞轮：badcase 收集 → 回归（#59）
- [ ] 插件经配置注册挂载，核心零 import（ADR-0007）
