# ADR-0008: 总结流式输出改为 Markdown 文档 + 实时渲染

- 日期：2026-08-21
- 状态：已接受

## 背景

ADR-0007 后总结 tab 流式期间展示的是 **LLM 原始 JSON 文本**（`summarize_stream`
用 `json_mode=True` 输出结构化 JSON，前端 `<pre>` 打字机逐字出现 `{"title": ...}`
原文）。用户反馈：流式期间应以 **Markdown 文档** 形式实时渲染（所见即所得），
而不是看到 JSON 原文。

## 决策

1. **LLM 直接输出 Markdown 总结文档**：`summarize_stream` 去掉 `json_mode`，
   prompt 要求严格按固定 Markdown 模板输出——`# 视频总结: {title}` / `> 时长` /
   `## 视频概述` / `## 章节时间线`（`### 章节标题 (MM:SS ~ MM:SS)` + `- 要点`）/
   `## 核心要点` / `## 结论`。`_chat_stream` 删除 `json_mode` 参数与
   `response_format` 分支；`_chat` 的 `json_mode` 保留（`generate_mindmap`
   非流式 JSON 仍用）。
2. **后端从 Markdown 解析回结构化 dict**：`parse_summary_text` 改为行级扫描
   Markdown 解析器（入口/签名不变，`task_manager.py` 调用点不动）——
   `# ` 行 → title（剥离「视频总结:」前缀）；`## ` 行按关键词识别小节（容忍
   LLM 微调措辞）；`### ` 行在章节小节内开新章，行尾 `(MM:SS ~ MM:SS)` 正则
   提取 start/end（兼容中文括号、分不补零，缺失 → 0.0 并从标题剥离）；
   `- `/`* ` 列表项归入当前章 points / key_points；其余文本行归入
   overview / conclusion（多行 `\n` 连接）。**缺「章节时间线」小节 → LLMError**
   （子任务 failed 可重试，对齐 `generate_mindmap` 缺 chapters 抛错）；
   overview / key_points / conclusion 缺失容忍空值。
3. **契约不变**：`Task.summary` dict 结构（title/overview/chapters/key_points/
   conclusion）、mindmap / qa / 导出 / summary 端点、SSE 帧协议（ADR-0007）
   全部不动——流式内容格式从 JSON 换成 Markdown，解析在流结束后进行。
4. **前端流式期间 marked 实时渲染**：running 分支从 `<pre>` 原始文本改为
   `<div class="md summary__stream" v-html="streamMdHtml">`，`streamMdHtml`
   对累积文本 `marked.parse`（打字机效果）；复用完成态 `.md` 样式，仅保留
   `margin-top` 微调。流式渲染、完成态渲染（`buildMarkdown`）、导出 MD
   （`_render_markdown`）三者同构，切换无感。
5. **标题措辞四处统一**：LLM 模板 / 解析器关键词 / 后端 `_render_markdown` /
   前端 `buildMarkdown` 统一为 `## 视频概述` / `## 章节时间线` / `## 核心要点` /
   `## 结论`（`_render_markdown` 原 `## 概述` / `## 核心知识点` 一并修正）。

## 后果

- 流式期间看到的是 Markdown 文档逐行渲染（标题/小节/要点实时出现），不再是
  JSON 原文
- 流式渲染、完成态渲染、导出 MD 三者内容一致，状态切换无感
- LLM 输出的文档结构 = 解析回结构化 dict 的契约：模板措辞变更需同步
  llm 模板 / 解析器 / `_render_markdown` / `buildMarkdown` 四处
- 非法判定语义：缺章节时间线 → 子任务 failed 可重试（与导图一致），其余
  小节缺失不阻塞（空值展示）
- 测试面：`FAKE_SUMMARY_MD`（Markdown 流）经真实解析器必须精确还原
  `FAKE_SUMMARY` dict（整 dict 相等契约不变）
