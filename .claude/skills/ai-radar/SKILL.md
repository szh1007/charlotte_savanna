---
name: ai-radar
description: 扫描 AI 热门领域（AI Agent、Vibe Coding、LangChain/LangGraph、LLM 模型、RAG/向量数据库、MCP、A2A 等）近一周的最新进展，生成结构化周报 Markdown 文件。当用户提到"AI 动态"、"AI 趋势"、"AI 新闻"、"AI 周报"、"AI radar"、"AI 领域更新"、"最近 AI 有什么新东西"、"AI 进展"、"跟上 AI 节奏"、"AI agent 更新"、"LangChain 更新"、"vibe coding 动态"时使用此 skill。即使只是泛泛问"AI 圈最近怎么样"或"最近有什么 AI 新闻"，也应触发。
---

# AI 雷达周报

追踪 AI 领域近一周的热门更新，覆盖 AI Agent、Vibe Coding、LangChain/LangGraph、LLM 模型、RAG、MCP 等方向，生成结构化周报。

## 扫描领域

每次运行需覆盖以下方向（每个方向一次 WebSearch）：

| # | 方向 | 搜索关键词（英文） | 关注点 |
|---|------|-------------------|--------|
| 1 | AI Agent 框架 | `AI agent framework updates 2026` | LangGraph, CrewAI, AutoGPT, Agent SDK, agent protocol |
| 2 | Vibe Coding / AI 编程 | `vibe coding AI programming tools updates 2026` | Cursor, Copilot, Claude Code, Windsurf, Bolt, Replit Agent, Devin |
| 3 | LangChain / LangGraph | `LangChain LangGraph new features update 2026` | 新版本、新特性、文档变更、生态变化 |
| 4 | LLM 模型 | `LLM model release update July 2026` | OpenAI, Anthropic, DeepSeek, Gemini, Llama, Mistral 新模型/新能力 |
| 5 | RAG / 向量数据库 | `RAG vector database updates 2026` | ChromaDB, FAISS, Pinecone, Weaviate, embedding 模型, RAG 范式 |
| 6 | MCP / A2A 协议 | `MCP protocol A2A agent protocol updates 2026` | Model Context Protocol, Agent-to-Agent 协议, 互操作标准 |
| 7 | 综合 / 其他 | `AI developer tools ecosystem news July 2026` | 补漏：热门开源项目、融资、社区趋势 |

搜索时使用 `WebSearch` 工具，每个搜索的 query 要具体，优先获取近一周的内容。如果搜索结果时效性不足，加上 `"this week"` 或具体日期范围缩小范围。

## 报告结构

ALWAYS 使用以下模板：

```
# AI 雷达周报 — YYYY-MM-DD

> 覆盖周期：YYYY-MM-DD ~ YYYY-MM-DD | 自动扫描生成

## 🎯 本周必看

（2-4 条最重要的更新，每条 1-2 句话 + 来源链接）

## 1. AI Agent 框架

| 项目/事件 | 类型 | 说明 | 来源 |
|-----------|------|------|------|
| ... | 新版本/新工具/文章 | 一句话要点 | [链接]() |

## 2. Vibe Coding / AI 编程

| 项目/事件 | 类型 | 说明 | 来源 |
|-----------|------|------|------|

## 3. LangChain / LangGraph

| 项目/事件 | 类型 | 说明 | 来源 |
|-----------|------|------|------|

## 4. LLM 模型

| 项目/事件 | 类型 | 说明 | 来源 |
|-----------|------|------|------|

## 5. RAG / 向量数据库

| 项目/事件 | 类型 | 说明 | 来源 |
|-----------|------|------|------|

## 6. MCP / A2A 协议

| 项目/事件 | 类型 | 说明 | 来源 |
|-----------|------|------|------|

## 7. 综合 / 其他

| 项目/事件 | 类型 | 说明 | 来源 |
|-----------|------|------|------|

## 📊 本周趋势关键词

（3-5 个关键词或短语，概括本周 AI 领域的热门方向）
```

## 执行步骤

### Step 1: 并行搜索

对 7 个方向分别发起 WebSearch。为了效率，所有搜索并行发起。每个搜索读完结果后，提取与近一周相关的信息条目。如果搜索结果信息过少（< 3 条），用不同的关键词再搜一次。

### Step 2: 筛选与整理

- 只保留近一周（7 天内）的更新。日期不明确的条目，如果上下文判断为近期则保留。
- 去重：同一事件多次出现时合并为一条，保留最详细的信息源。
- 每个方向保留 3-8 条，少于 3 条说明该方向本周无重大更新。
- 类型标记：新版本 / 新工具 / 新模型 / 新特性 / 文章/教程 / 融资/公司 / 协议/标准 / 其他

### Step 3: 提取"本周必看"

从所有条目中选出最重要的 2-4 条放入"本周必看"板块。选择标准：
- 对开发者工作流有直接影响（如 Claude Code 更新、LangChain 大版本）
- 重大模型发布（如 GPT-5、Claude 4）
- 行业里程碑事件（如重大融资、开源发布、协议变更）

### Step 4: 提炼趋势关键词

根据本周所有条目，归纳 3-5 个趋势关键词。例如：`Agent 互操作`、`本地优先`、`多模态普及` 等。

### Step 5: 保存与展示

1. 将报告保存为 `docs/skills_md/ai-radar/YYYY-MM-DD.md`（当前日期）。
2. 在终端输出简版摘要（🎯本周必看 + 📊趋势关键词 + 各方向条目数）。
3. 提示完整报告路径。

### Step 6: 展示原则

- 终端输出精炼：只放"本周必看"和"趋势关键词"以及各方向条目计数，完整表格请用户查看文件。
- 每条说明控制在 30 字以内，不写长段落。
- 所有信息必须附带来源链接 — 没有来源的条目不要写入报告。
- 不确定时效性的条目标注"时间待确认"。
