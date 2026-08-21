# 闯关交互归 Django（无 LLM 的交互不跨服务）

初案闯关交互放 FastAPI。分析发现首版闯关（判分 / 反馈 / 游戏化状态更新）是纯规则逻辑 + 预生成讲解展示，**无 LLM 参与**，而题目与答题数据全在 Django。决定：首版闯关交互归 Django，FastAPI 只做 AI 管道；Boss 战对话式（真 LLM 流式交互）二期入 FastAPI。RAG 全链路（索引 / 检索 / 生成）属 AI 能力，归 FastAPI 不变。

**Status**: accepted

**Considered Options**:
- 闯关交互放 FastAPI（初案）：为二期 Boss 战统一入口，但首版需"前端 → FastAPI → Django"两层跳转执行纯规则逻辑，延迟与耦合无收益
- 闯关交互归 Django（采纳）：职责清晰 —— Django = 状态与数据，FastAPI = AI 能力

**Consequences**: Boss 战接入时前端从 Django 答题流切换到 FastAPI 流式通道，需预留前端交互层抽象。
