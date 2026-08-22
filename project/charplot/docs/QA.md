# CharPlot 架构问答（QA）

> 用途：项目架构设计的答疑索引，快速查阅「架构为什么这么设计」。
> 收录范围：仅项目架构设计相关问题（技术栈分工 / 流程链路 / 关键决策），通用技术知识不收录。
> 来源：2026-08-22 架构梳理与审问会话。文档链接均为仓库内相对路径，可直接跳转。

---

## 1. 技术栈与三件套分工

### Q1. LangGraph 只是用于 RAG 吗？题目生成用什么框架？

**不是。** LangGraph 不负责 RAG，它负责**知识管道编排**；题目生成用的是 **DeepAgents（出题 subagent）**，不是 LangChain。三件套在 FastAPI 侧各司其职：

| 组件 | 职责 | 落点 |
|------|------|------|
| LangGraph | 知识管道编排（解析 → 搜索 → 解构） | `pipeline/` |
| DeepAgents | 智能执行 subagent（搜索 / 解构 / 出题） | `agents/` |
| LangChain | RAG 组件（切分 / embedding / 检索 / rerank） | `rag/` |

**文档指引：**
- [DESIGN.md §2 架构图](./DESIGN.md#L25) — FastAPI 侧四条职责分列，可对照
- [DESIGN.md §3 目录结构](./DESIGN.md#L50) — pipeline / agents / rag 三目录职责
- [CONTEXT.md Q9](./CONTEXT.md#L85) — FastAPI = AI 能力端「三件套」实践

### Q2. LangChain / LangGraph / DeepAgents 分别负责什么？

一句话版：**LangGraph 决定"怎么走"（流程），DeepAgents 负责"谁来干"（智能体），LangChain 提供"用什么干"（零件）**。

| | LangGraph | DeepAgents | LangChain |
|---|---|---|---|
| 管什么 | 流程顺序与状态传递 | 单步决策（调哪个工具、产出什么） | 组件与集成（解析/切分/检索/模型封装） |
| 特点 | 确定性编排（workflow） | 智能决策（agent loop） | 零件复用 |

DeepAgents 底层本身基于 LangGraph 编译图构建（`create_deep_agent` 返回 `CompiledStateGraph`），本项目是"大图套小图"：外层管道图编排流程，节点内部跑 DeepAgents agent。

**文档指引：**
- [DESIGN.md §2 架构图](./DESIGN.md#L25) — 管道与 agents 的嵌套关系
- [SPEC.md §4 技术栈](./../../../.scratch/charplot/SPEC.md#L60) — FastAPI 侧三件套声明

### Q3. 为什么 LangGraph 管道里还嵌套 DeepAgents，不重复吗？

**不重复，两个抽象层级各管各的：**

- **外层 LangGraph = 固定流程**：解析 → 搜索 → 解构，步骤写死、有先后依赖、可失败重试 → 保证流程可控
- **内层 DeepAgents = 开放任务**：搜索/解构/出题需要 agent 自主判断（搜什么关键词、拆哪些知识点）→ 保证单步灵活

这就是 **workflow + agent 混合架构**：流水线轨道（LangGraph）决定走向，轨道上会思考的机器（DeepAgents）决定怎么干活。佐证：不需要智能的环节（如文档解析）直接用普通函数写在节点里，只有需要自主决策的环节才套 DeepAgents。

**文档指引：**
- [DESIGN.md §3 目录结构](./DESIGN.md#L64) — `pipeline/`（LangGraph StateGraph）与 `agents/`（DeepAgents subagents）分工
- [issue 07](./../../../.scratch/charplot/issues/07-real-knowledge-pipeline.md#L7) — LangGraph 编排四阶段 + DeepAgents 承担检索环节

### Q4. LangGraph 和 LangChain 的区别是什么？本项目 RAG 为什么不用 LangGraph 编排？

| | LangChain | LangGraph |
|---|---|---|
| 定位 | 组件库 + 集成层 | 低层编排框架 + runtime（节点/边/状态/循环/中断） |
| 抽象 | LCEL 线性管道（`\|`） | StateGraph 有向图 |

本项目的 RAG 分两半，**都不需要图编排**：
- **索引**（切分/embedding/入 Milvus）= 确定性批处理（全量重建），任务系统跑批即可
- **检索**（混合检索 + rerank）= 单次调用管道，组件管道即可

需要智能编排的环节（解构/出题）已经在 LangGraph 管道里，且这两个环节恰恰"吃检索"——RAG 检索是被管道节点调用的组件，不是自己再画一张编排图。**"用 LangGraph 写 RAG"指 agentic RAG（检索质量评估/失败重写重试/按需检索），属二期规划。**

**文档指引：**
- [SPEC.md §7.2 RAG 架构](./../../../.scratch/charplot/SPEC.md#L138) — 各环节实现要点
- [CONTEXT.md Q21](./CONTEXT.md#L102) — Agentic RAG 二期决策
- [ADR-0002](./adr/0002-unified-knowledge-pipeline.md#L8) — 检索源可插拔

### Q5. 用 LangGraph 还是 LangChain 实现 RAG，怎么选？

判定标准：**RAG 流程需要 LLM 决策 / 循环重试 / 跨步骤状态吗？**

| 问题 | 是 → | 否 → |
|---|---|---|
| 需要 LLM 判断检索质量、要不要检索？ | LangGraph（agentic RAG） | LangChain（管线式） |
| 需要循环重试（失败重写再查）？ | LangGraph | LangChain |
| 需要跨步骤状态 / 记忆 / 人工介入？ | LangGraph | LangChain |

本项目首版 = 管线式（LangChain 组件，混合检索 + rerank），二期按需升级 agentic RAG（在 `rag/` 模块内演进，外部接口不变）。

**文档指引：**
- [CONTEXT.md Q21](./CONTEXT.md#L102) — 二期演进路径
- [SPEC.md §7.2 RAG 架构](./../../../.scratch/charplot/SPEC.md#L138)

---

## 2. 架构与流程链路

### Q6. 本项目完整的 LLM / AI 能力全景？

**五条流程（四条有 LLM，一条无）：**

```
A. 知识获取管道（LangGraph 编排 + DeepAgents 干活）
   解析(无 LLM) → 主内容分析(LLM) → 联网搜索增强(DeepAgents 搜索 agent) → 图谱解构(DeepAgents 解构 agent)
B. RAG 知识库（LangChain 管线式，被动服务）
   索引(批处理) → 检索: query rewriting(LLM) → 混合检索 → rerank → Top-K
C. 题目生成（DeepAgents 出题 subagent）
   知识点 + 检索片段 → 题目 JSON（选择/判断/填空 + 讲解 + 来源引用）
D. 闯关答题（Django 纯规则，无 LLM）
   判分/心动值/XP/间隔复习混入全部规则逻辑，LLM 不参与
E. LLM 状态总结（直接 LLM 调用，无 agent）
   Dashboard 统计聚合 → LLM 文字报告（强项/弱项/建议）
```

**容易被忽略的 LLM 参与点**：query rewriting（检索前改写）、主内容分析、图谱骨架生成（两轮解构第一轮）、讲解生成（随题目预生成）、LLM 状态总结。

**文档指引：**
- [DESIGN.md §2 架构图](./DESIGN.md#L25) — 五条流程落点
- [DESIGN.md §4.2 AI 接口表](./DESIGN.md#L109) — 各流程入口
- [SPEC.md §6 核心流程](./../../../.scratch/charplot/SPEC.md#L86)
- [issue 13](./../../../.scratch/charplot/issues/13-llm-status-summary.md#L7) — LLM 状态总结（裸 LLM 调用）

### Q7. RAG（流程 B）被流程 A / C 调用，调用什么、提供什么？

**同一个接口，两种用途：** `POST /ai/kb/search`，输入 `{kb_id, query}`，输出 `{chunks[]}`（带来源 metadata 的检索片段，**不是答案**）。

| 调用方 | 用途 | 拿它干什么 |
|--------|------|-----------|
| A 解构 | RAG 两轮解构（第一轮概览检索建骨架，第二轮逐知识点细化依赖） | 生成知识图谱 |
| C 出题 | 以知识点为 query 检索 | 生成题目，且**只基于检索片段**（幻觉防护①） |

关键设计：**B 只提供片段不生成内容，生成（图谱/题目/讲解）全在 A/C 的 LLM 环节**——把"生成依据"与"生成动作"拆开，实现 Q8 幻觉防护。

**文档指引：**
- [DESIGN.md §4.2 API 表](./DESIGN.md#L118) — `/ai/kb/search` 标注"管道内部/调试"
- [SPEC.md §7.3 幻觉防护](./../../../.scratch/charplot/SPEC.md#L150) — 三道防线
- [CONTEXT.md Q19a](./CONTEXT.md#L100) — 解构/出题都吃检索

### Q8. 用户输入材料的旅程，材料会入库 RAG 吗？

**不会。** 入库 Milvus 只发生在**管理员预建知识库**这一条路（Q18）。用户旅程的输入材料以「输入快照」存在 `charplot_journey` 表，但不进向量库。两条旅程路径对比：

| | ① 用户输入材料/主题 | ② 知识库驱动（点主题卡片） |
|---|---|---|
| 管道①阶段 | 归一化解析 | RAG 两轮解构 |
| 知识来源 | 解析文本 + 联网搜索增强 | Milvus 检索片段 |
| 入库 Milvus？ | 否 | 是（管理员已预建） |
| 出题依据 | 可插拔检索源（网络/Context7/文档） | 知识库检索 |

设计理由：知识库是"产品内容层"（一份内容多用户/多旅程复用），用户材料是一次性私有输入；材料旅程靠"联网搜索增强"补知识面（ADR-0002），不依赖入库。

**文档指引：**
- [CONTEXT.md Q18 / Q19](./CONTEXT.md#L95) — 知识库定位与主题关系
- [SPEC.md §6.2 流程](./../../../.scratch/charplot/SPEC.md#L102) — 两条路径的分叉点
- [SPEC.md §7.1 检索源可插拔](./../../../.scratch/charplot/SPEC.md#L130)

### Q9. 材料旅程用完即弃？用户如何查看历史旅程？中途退出能继续吗？

**"弃"的只是 Milvus 向量库，旅程本身全程持久化。** 查看靠 Django 表 + API：

| 层 | 表 | 查看方式 |
|----|----|---------|
| 旅程 | `charplot_journey`（输入快照/来源类型/图谱） | GET /api/journeys → /api/journeys/{id} |
| 图谱 | `charplot_chapter` / `charplot_knowledge_point` | /api/journeys/{id}/skill-tree |
| 关卡/题目 | `charplot_level` / `charplot_question` | /api/journeys/{id}/levels |
| 答题 | `charplot_attempt`（逐题事实） | 个人主页 / Dashboard |

**中途退出可以继续**：PRD B-3 验收标准明确"可随时回到未完成的旅程继续"；题目渐进生成 + 预生成机制（Q5）保证下次回来题目已就绪。

**文档指引：**
- [PRD B-3](./../../../.scratch/charplot/PRD.md#L59) — 旅程列表验收标准
- [SPEC.md §8 数据模型](./../../../.scratch/charplot/SPEC.md#L158) — 全表定义
- [DESIGN.md §4.1 API 表](./DESIGN.md#L87)

### Q10. "RAG 两轮解构"和"可插拔检索源"是什么意思？

**两轮解构**（知识库场景的解构策略）：知识库可能几十万字，一次全塞给 LLM 建图谱会 token 超限、关键信息被淹没，所以分两步——
1. **第一轮概览检索**：拿文档结构/摘要 → LLM 建图谱骨架（章节 + 知识点粗结构）
2. **第二轮知识点细化**：对每个知识点精检索 → LLM 判断前置依赖边

图谱是技能树/关卡/间隔复习的锚点（Q4），**骨架错了后面全错**，两轮 = 先粗后细。

**可插拔检索源**（统一检索抽象）：管道环节不写死"去哪个地方找"，统一调 `retrieve(query)`，背后接适配器：

```
网络搜索(Tavily) / Context7(官方文档) / 知识库(Milvus) / 文档(旅程解析文本)
```

好处：解耦（管道不关心来源）、可组合（搜索增强 = 网络 + 知识库交叉验证）、可替换（换服务商零改动）。

**文档指引：**
- [CONTEXT.md Q19a](./CONTEXT.md#L100) — 两轮解构决策
- [SPEC.md §6.2](./../../../.scratch/charplot/SPEC.md#L115) — 知识库解构两轮流程
- [ADR-0002](./adr/0002-unified-knowledge-pipeline.md#L10) — 检索源可插拔理由

---

## 3. 关键决策

### Q11. 关卡中途退出、心动值、重开的规则是什么？

**Q22 决策（2026-08-22）：**

```
进入关卡 → 读 level 进度字段（答到第几题）+ 剩余心动值 → 从断点续答
  答错 → 心 -1 → 心 = 0 → 本关失败 → 重开（题目、心重置）
  中途退出 → 进度 + 剩余心落库 → 下次进入从断点继续
重开 → 新 Attempt 照常记录，历史 Attempt 保留不覆盖（供掌握度分析）
```

- `charplot_level` 增加进度字段（答到第几题/已通关）+ 剩余心动值
- `charplot_attempt` 逐题事实记录；重开产生新记录，历史保留

**文档指引：**
- [CONTEXT.md Q22](./CONTEXT.md#L102) — 决策记录
- [SPEC.md §8 数据模型](./../../../.scratch/charplot/SPEC.md#L165) — level / attempt 字段说明
- [issue 05 验收项](./../../../.scratch/charplot/issues/05-level-quiz-and-settlement.md#L9) — 断点续答与重开验收标准

### Q12. 二期 Agentic RAG 是什么？和首版 RAG 什么关系？

**首版 = 管线式 RAG**（LangChain 组件：混合检索 + rerank 必配），保持不动；**二期 = Agentic RAG**（Q21）：用 LangGraph 编排更智能的检索流程（类似 RAGFlow）——LLM 检索质量评估、失败重写重试（corrective RAG）、按需检索（adaptive RAG）。在 `rag/` 模块内演进升级，**外部接口 `{kb_id, query} → {chunks[]}` 不变**，A/C 调用方零感知。

**文档指引：**
- [CONTEXT.md Q21](./CONTEXT.md#L102) — 决策记录
- [DESIGN.md §9 Phase 2 能力表](./DESIGN.md#L196)
- [SPEC.md §12 二期清单](./../../../.scratch/charplot/SPEC.md#L202)

---

## 4. 易混淆点速查

| 易混淆点 | 正解 |
|---------|------|
| LangGraph 管 RAG？ | 否，管知识管道编排；RAG 是 LangChain 组件 + 二期才上 LangGraph |
| 题目生成用 LangChain？ | 否，DeepAgents 出题 subagent |
| 用户材料入库 RAG？ | 否，只有管理员预建知识库入 Milvus |
| 闯关答题有 LLM？ | 否，判分/讲解展示是纯规则 + 预生成（ADR-0003） |
| RAG 返回答案？ | 否，返回检索片段（chunks），生成在 A/C 的 LLM 环节 |
| 知识库旅程和材料旅程解构方式？ | 前者两轮解构（Milvus），后者解析+搜索增强（可插拔检索源） |

---

> 维护者：Claude Code (charlotte) ｜ 2026-08-22
