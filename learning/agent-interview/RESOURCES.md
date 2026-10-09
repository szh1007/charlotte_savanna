# AI Agent 面试 Resources

> 可信度分层是这份清单的第一原则。这份语料里**培训机构内容占比很高**，而真实面经与培训整理混在一起流传 —— 每条都标了类型与可信度，引用时按标注降级使用。

## Knowledge

### 一手面经（发帖者自述的现场记录，含追问链）

- [linux.do《面试拷打了十几家 Agent 岗位》](https://linux.do/t/topic/2365650)
  作者自述全程录音 + AI 整理（8 年前端背景、广州、18–25k、14 家公司脱敏）。**追问链最完整的一份**，含「SSE 断线续传」「任务队列崩溃后不丢」「Token 成本降幅」等工程题与「用户量/日活/降本百分比」这类业务数字追问。用在哪：工程化与成本、业务口径、Harness 概念（HQ-36）。
- [牛客《百度一面 后端 Go/Agent》（53 题全量）](https://m.nowcoder.com/discuss/922907945363349504)
  **本批材料里最细的一份**，含具体追问链与作者自评。岗位是自动化红队 Agent。用在哪：上下文压缩的细节追问（Q28–30）、记忆冲突（Q17）、多 Agent 消息传递与校验重跑（Q24–27）、评测过程指标（Q35–40）、多 Pod 一致性哈希（Q32–34）。
- [知乎《某东本部 大模型应用开发工程师 面试题记录（25–40k）》](https://zhuanlan.zhihu.com/p/2015848695493574787)
  9 题原话，2026-03-13 编辑，**一份难得的社招中高级样本**。用在哪：MCP 调用生命周期（第 7 题）、chunking 与多路召回（第 4–5 题）、幻觉参数防护与幂等/分布式事务回滚（第 8–9 题）。
- [牛客《小红书 Agent 开发实习一面凉经 + 百度凉经》](https://m.nowcoder.com/feed/main/detail/e319aadc79a9479397a6661a7f5ca088)
  两场面试的逐题记录。用在哪：**框架选型题的原始措辞**（「为什么用 langgraph」「langgraph 比较老了，有没有考虑新的思路」）、子图拆分与路由决策、AI Coding 协作。
- [牛客《大模型应用面经合集（已拿 offer）》](https://m.nowcoder.com/feed/main/detail/0503d45073494879bbe91f5fccd6efbe) · [牛客《大模型应用开发面经（5 年经验）》](https://m.nowcoder.com/feed/main/detail/129eaa1c20444651ac3b932e200d3da4)
  **两份高度疑似同稿**（措辞近乎逐字一致，含相同拼写「langchian」）——统计频率时按**一份**计。用在哪：MCP vs Function Call、为什么手搓 agent 而不是用框架、Function Call 是怎么训练的、温度/top-p/top-k。
- [面灵 AI 面经库《快手 AI 应用二面》（2026-09，牛客原帖转载）](https://mj.mianlingai.com/interview/kuaishou-ai-app-2-2916634/)
  用在哪：**效果评估与人效衡量口径**、上下文方案是「修补还是可复用」这条区分实习与社招的问法、部署了多少卡/推理成本是否合理。

### 二手汇总题库（题量与标签可观，统计口径不可验证）

- [面灵 AI《265 道大厂真题按主题归类（2026 版）》](https://www.mianlingai.com/topics/llm-agent-interview-questions-2026/)
  **覆盖面最广的一份**，九大主题逐题带公司标签。**注意**：平台主业是面试辅助工具，内容有营销属性；公司标签仅作参考，不能当出题名单。用在哪：几乎每个主题的代表题都能在这里找到一条。
- [GitHub AgentGuide《AI Agent 面试题库 · 核心篇》](https://github.com/adongwanai/AgentGuide/blob/main/docs/04-interview/03-agent-questions.md)
  52 题逐题带难度/岗位/公司标签（含「字节(真题)」标注），标签机制未披露。用在哪：快速扫题面，看哪些题是你没准备过的措辞。
- [GitHub datawhalechina/hello-agents《面试问题总结》](https://github.com/datawhalechina/hello-agents/blob/main/Extra-Chapter/Extra01-面试问题总结.md)
  个人整理的八股合集。用在哪：Lost in the Middle、知识图谱 vs 向量检索、红队测试、Agent 评估为何比 LLM 评估更难。
- [牛客《2025–2026 AI 应用开发与 Agent 大厂面试高频问题》（八股精）](https://m.nowcoder.com/discuss/927907189061087232)
  自报「约 2828 条真实面试记录」，**无法独立验证**（同页含「80 万+真题」营销文案）。用在哪：看「真题示例」的措辞风格；不要引用它的统计。
- [GitHub zero2Agent 绿皮书《Agent 面试 500 问》](https://github.com/ranxi2001/zero2Agent)
  优点：带日期与轮次的真题（如「字节社招一面 2026-08-23」）。**注意**：本工作区只读了 README，PDF 未逐页精读，其中真题引用来自搜索引擎摘要 —— 引用前需自行复核原文。

### 官方一手工程文档（引用行业概念时的唯一依据）

- [Mitchell Hashimoto《My AI Adoption Journey》（2026-02-05）](https://mitchellh.com/writing/my-ai-adoption-journey)
  「Engineer the Harness」这一提法的**源头**。用在哪：被问「Harness 是什么、谁提的」时的出处。
- [OpenAI《Harness engineering: leveraging Codex in an agent-first world》](https://openai.com/index/harness-engineering/)
  厂商侧对 harness engineering 的官方表述。用在哪：把「我做的事」接进行业叙事。
- [Anthropic《Effective harnesses for long-running agents》](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
  长运行 agent 的 harness 设计。用在哪：Context Reset / 长任务上下文策略的官方依据（与你 `01-context-compaction.md` 底稿同域）。
- [Cognition《Devin + Sonnet 4.5: lessons and challenges》](https://cognition.ai/blog/devin-sonnet-4-5-lessons-and-challenges)
  「Context Anxiety」这个说法的出处。用在哪：解释「快到窗口上限时模型表现为何变差」时的行业术语来源。

### 厂商官方工程文档（英文侧，2026-10 逐篇抓取核对）

> **这组是本工作区可信度最高的一类。** 面试里谈口径时优先引它们 —— 它们是「行业共识」这个说法的唯一凭据。除标注外，均已被实际抓取并通读。

- [Anthropic《Building Effective AI Agents》（2024-12）](https://www.anthropic.com/engineering/building-effective-agents)
  **分类学的源头**：workflow = 「LLMs and tools are orchestrated through predefined code paths」；agent = 「LLMs dynamically direct their own processes and tool usage」；五个 workflow 模式（prompt chaining / routing / parallelization / orchestrator-workers / evaluator-optimizer）。附录的工具工程（poka-yoke、工具当 prompt 写）同样常被引用。用在哪：所有「workflow vs agent」类的题。
- [Anthropic《Effective context engineering for AI agents》](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
  四个术语的出处：<b>context rot</b>（token 越多召回越差，根因是 n² 注意力与训练分布）、系统提示的 <b>right altitude</b>（在脆弱硬编码与过度笼统之间）、<b>JIT + progressive disclosure</b> 检索、长任务三件套（compaction / structured note-taking / sub-agent 蒸馏，子 agent 探索数万 token 只回 1,000–2,000）。用在哪：上下文压缩与记忆题的权威口径。
- [Anthropic《Writing effective tools for AI agents》](https://www.anthropic.com/engineering/writing-tools-for-agents)
  工具不是越多越好（合并 / 命名空间）；返回 token-efficient 信息；**Claude Code 把工具响应截断到约 25,000 token —— 截断本身是引导行为的手段**；错误响应要「教」模型纠错。用在哪：工具设计题、以及「工具返回太长怎么办」。
- [Anthropic《How we built our multi-agent research system》](https://www.anthropic.com/engineering/multi-agent-research-system)
  **多智能体唯一的硬数据来源**：比单 agent 提升 90.2%；多 agent 约耗 15 倍 chat token（单 agent 约 4 倍）；eval set 从约 20 条起步；子 agent 输出写文件系统以避免「传话游戏」；彩虹部署灰度。用在哪：多 agent「什么时候值得 / 成本多少 / 怎么评」四个问题一篇答完。
- [Anthropic《Code execution with MCP》](https://www.anthropic.com/engineering/code-execution-with-mcp)
  让 agent 写代码调工具而非把工具定义塞进上下文：**一次案例 150,000 → 2,000 token**。用在哪：MCP 规模化、工具集膨胀、token 预算题的最强数据点。
- [Anthropic《Scaling Managed Agents: Decoupling the brain from the hands》](https://www.anthropic.com/engineering/managed-agents)
  agent 基础设施拆成三个接口：<b>session</b>（append-only 事件日志）/ <b>harness</b>（跑循环的壳）/ <b>sandbox</b>（执行环境）；pets vs cattle；<b>context anxiety 的准确定义与它随模型换代消失的记录</b>；凭证永不进沙箱（vault + proxy）。用在哪：服务端架构题、「工程手段会失效」这条叙事。
- [Claude Agent SDK 官方文章《Building agents with the Claude Agent SDK》（2025-09）](https://claude.com/resources/articles/building-agents-with-the-claude-agent-sdk)
  loop 三段式（gather context → take action → verify work）；<b>agentic search vs semantic search</b>（先 grep/glob 类，规模与实时性需要时才上语义检索）；验证闭环三法（规则 / 视觉 / LLM 复核）。用在哪：loop 分层、检索选型。
- [OpenAI《A practical guide to building agents》（PDF）](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
  OpenAI 口径的 agent 三要素（Model + Tools + Instructions）；**manager pattern vs decentralized handoffs**；guardrails 分类；optimistic execution。用在哪：与 Anthropic 口径对照（两者定义不同，LangChain 的 Harrison Chase 曾公开批评 OpenAI 定义过泛，见下）。
- [OpenAI Agents SDK《Agent orchestration》](https://openai.github.io/openai-agents-python/multi_agent/) · [《Guardrails》](https://openai.github.io/openai-agents-python/guardrails/) · [《Sessions》](https://openai.github.io/openai-agents-python/sessions/)
  **agents-as-tools vs handoffs** 的官方判据表（何时用哪个）；三类护栏 + tripwire 机制 + 并行 vs blocking 的取舍；session memory 与 `previous_response_id` 互斥（客户端托管 vs 服务端托管二选一）。用在哪：编排与护栏题的官方术语。
- [LangChain《How to think about agent frameworks》（Harrison Chase，2025-04）](https://www.langchain.com/blog/how-to-think-about-agent-frameworks)
  **框架选型题最有信息量的一篇**：大多数所谓 agent 框架只是「agent abstractions」而非编排框架；抽象层会遮蔽上下文控制；附 12 个框架的对比表。「三方口径打架」时引它的对照分析。用在哪：「要不要用框架 / 怎么选」。
- [LangChain / LangGraph《Workflows and agents》](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
  与 Anthropic 相同的模式分类学，每个模式给 LangGraph 实现（含 ToolNode 等 primitives）。用在哪：把 pattern 落到代码、术语对齐。
- [MCP 官方规范（当前版本 2026-07-28）](https://modelcontextprotocol.io/specification/2026-07-28)
  JSON-RPC 2.0；三角色 Hosts / Clients / Servers；服务端三类 feature（Resources / Prompts / Tools）+ 客户端侧 <b>Elicitation</b>；基线协议是 **stateless、self-contained 请求** + 每请求 capability negotiation；可选 extensions 机制。用在哪：被问「MCP 协议本身」时往上打一层 —— 比「agent 的 USB-C」深一级。
- [Claude Code《Best practices》](https://code.claude.com/docs/en/best-practices)
  第一原则「context window 是最重要资源」；**验证闭环的分级**（单 prompt → 条件 → Stop hook 硬门 → 独立验证 subagent）。用在哪：harness 设计与「agent 何时可无人值守」。
- **未验证**：[Google / Kaggle《Agents》白皮书](https://www.kaggle.com/whitepaper-agents) 与 [《Introduction to Agents》](https://www.kaggle.com/whitepaper-introduction-to-agents) —— 页面元数据已核对（作者与页数），但 PDF 正文因 Google Drive 抓取被阻断**未能下载验证**。引用其具体内容前必须自行下载核对。

### 英文面试题库（英文侧面试题，2026-10 抓取核对）

- [Aced（前 Exponent）《45+ AI Engineer Interview Questions（2026）》](https://www.aced.io/blog/ai-engineer-interview-questions)
  真题带公司归属（OpenAI / Anthropic / Scale AI / Sierra / Glean / xAI / Databricks / Perplexity），**候选人口述**（非官方题库）。用在哪：看前沿实验室的 AI system design 轮长什么样（GPU batching、配额管理、保险理赔 agent 的成本控制）。
- [Aced《Sierra Agent Engineer Interview Guide》](https://www.aced.io/guides/sierra-agent-engineer-interview)
  完整流程 + take-home 原话口径：**「写一个干净的 agent loop 直接调用模型和工具，不要用框架」** —— 对你是强项信号。用在哪：了解英文侧 take-home 的真实形状。
- [AI Engineering Field Guide（社区汇总 repo）](https://github.com/alexeygrigorev/ai-engineering-field-guide)
  130+ 来源的理论题库 + AI system design 章（明列哪些公司设专门 AI system design 轮）+ 趋势章与 take-home 分析。用在哪：扫英文侧题目面，比逐站找效率高。
- [awesome-generative-ai-guide · AI Engineer 角色](https://github.com/aishwaryanr/awesome-generative-ai-guide)
  含一句常被引用的口径：「现代 AI engineer 技术 loop 约 75% 是 RAG、agents、evals、LLM system design」。
- [InterviewQuery《AI Engineer Interview Questions》](https://www.interviewquery.com/p/ai-engineer-interview-questions)
  偏传统 ML 面，作对照用（看哪些题属于「经典 ML 那 25%」）。
- **未验证**：Reddit 两个 thread、Blind、Sheldon 的 agent-interviews 站（**内容密码保护**）；`levels.fyi` **不存在**面试题库产品（实测 404，只作薪资来源）。

### 框架 / 协议 / 评测的一手来源（2026-10-10 实抓核对）

> 生态事实的完整速查已压成卡片：[`reference/ecosystem-2026-10.html`](reference/ecosystem-2026-10.html)。这里只列来源本身。

- [LangChain《How to think about agent frameworks》](https://www.langchain.com/blog/how-to-think-about-agent-frameworks)
  **「2026 是 harness 之争」的最硬证据**：LangChain 官方文档自己的分层口径是 Deep Agents = batteries-included harness / LangChain = customizable harness / LangGraph = low-level orchestration。附 12 个框架对比表。用在哪：框架选型题、以及「你为什么手搓」的正名。
- [LangGraph《Workflows and agents》](https://docs.langchain.com/oss/python/langgraph/workflows-agents) · [LangChain overview（分层口径）](https://docs.langchain.com/oss/python/langchain/overview)
  checkpointer（thread 级短期）vs Store（跨 thread 长期）、`interrupt()` + `Command(resume=…)`、middleware 三件（Summarization / PII / HumanInTheLoop）。**含一条易被问倒的官方口径**：最新图定义会立即作用于已存在的 thread 状态。
- [Deep Agents《subagents》](https://docs.langchain.com/oss/python/deepagents/subagents) · [overview](https://docs.langchain.com/oss/python/deepagents/overview)
  **context quarantine** 的两种模式（isolate / fork）—— 这是「subagent 为什么省 token」的标准答案。
- [OpenAI Agents SDK《Agent orchestration》](https://openai.github.io/openai-agents-python/multi_agent/) · [《Guardrails》](https://openai.github.io/openai-agents-python/guardrails/) · [《Sessions》](https://openai.github.io/openai-agents-python/sessions/) · [《Context》](https://openai.github.io/openai-agents-python/context/)
  `handoff()` 移交控制权 vs `Agent.as_tool()` 保留控制权；三类护栏 + **tripwire**（触发即终止 run，不是让模型重试）；session 与 `previous_response_id` 互斥。
- [Anthropic《Demystifying evals for AI agents》（2026-01-09）](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
  **评测的标准词汇表**：task / trial / grader / transcript / harness，以及 **pass@k（能力上限）vs pass^k（可靠性）**、capability vs regression evals。用在哪：评测题的口径对齐 —— 你缺的正是这套词。
- [MT-Bench 论文《Judging LLM-as-a-Judge》(arXiv 2306.05685)](https://arxiv.org/abs/2306.05685)
  **LLM-as-judge 三偏差的原始出处**（position / verbosity / self-enhancement）+「GPT-4 judge 与人类偏好一致率 >80%，与人类间一致率同级」。用在哪：你的 ADR-0021「刻意不做 judge」需要一个**知道对方立场后**的论证 —— 这篇就是对方的立场。
- [Hamel Husain《Your AI Product Needs Evals》](https://hamel.dev/blog/posts/evals/)
  实战派的评测方法论（业界引用率很高）。用在哪：把 `judges.py` 的实践摆进行业坐标系。
- [Manus《Context Engineering for AI Agents》](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
  六条实战经验，第一条「**KV-cache 命中率是 agent 第一指标**」与你自己底稿 `01-context-compaction.md` 的 Q4 **同源** —— 你已有准备，缺的只是这个外部背书。
- [Lost in the Middle(arXiv 2307.03172)](https://arxiv.org/abs/2307.03172) · [NoLiMa(arXiv 2502.05167)](https://arxiv.org/abs/2502.05167)
  「有窗口 ≠ 能利用」的两篇关键论文：信息位置呈 **U 型**；去掉字面匹配后 32K 处模型普遍跌破短上下文基线的 50%。用在哪：回答「长上下文能替代 RAG 吗」。
- [MCP 官方规范 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) · [弃用清单](https://modelcontextprotocol.io/specification/2026-07-28/deprecated) · [官方博客发布说明](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
  **无状态化大改版**：`server/discover` 取代 initialize 握手、无 `Mcp-Session-Id`、MRTR（`input_required`）、`subscriptions/listen`；弃用 roots / sampling / logging / DCR / HTTP+SSE。
- [A2A v1.0 发布公告（2026-03-12）](https://a2a-protocol.org/latest/blog/2026/03/12/a2a-protocol-ships-v10-production-ready-standard-for-agent-to-agent-communication/) · [规范](https://a2a-protocol.org/latest/specification/)
  官方定位「MCP = vertical，A2A = horizontal」。
- [OpenAI《A practical guide to building agents》（PDF）](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) · [Anthropic《Building Effective AI Agents》](https://www.anthropic.com/engineering/building-effective-agents) · [Anthropic《Effective context engineering》](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) · [Anthropic《Code execution with MCP》](https://www.anthropic.com/engineering/code-execution-with-mcp) · [Anthropic《Scaling Managed Agents》](https://www.anthropic.com/engineering/managed-agents) · [Claude Code《Best practices》](https://code.claude.com/docs/en/best-practices) · [Claude Code context window 文档](https://code.claude.com/docs/en/context-window)
  见上文「厂商官方工程文档」小节。**新增两条**：Claude Code 的**压缩存活表**（system prompt 保留；CLAUDE.md / plan 压缩后从磁盘重注入；最近 5 个文件重读；>5,000 token 的文件只留路径引用）与《Managing tool context》的四法（tool search / programmatic tool calling / prompt caching / context editing）。

### 培训机构内容（**可信度低**，仅作「2026 叙事风向」的观察窗口）

> 保留它们不是为了题目，而是因为它们**最快反映面试官用的新词**。题目真实性无法独立验证，引用时必须标明来源性质。

- [卡码笔记《Harness Engineering 大厂面试题汇总》](https://notes.kamacoder.com/interview/llm/harness_interview.html) —— 其引用的概念出处（上面四条一手来源）已核对存在；题目本身为站方整理。
- [卡码笔记《Loop 详解：从 ReAct 到 Loop Engineering》](https://notes.kamacoder.com/interview/llm/loop_engineering_interview.html) —— 六连追问（「ReAct 不就是个 while 循环吗」）反映的是 2026 的提问叙事。
- [卡码笔记《字节 Agent 开发四面面经》](https://notes.kamacoder.com/interview/llm/byte-agent-4.html) · [《字节 Agent 应用开发实习一面（番茄小说）》](https://notes.kamacoder.com/interview/llm/bytedance_fanqie_agent_intern_interview.html) · [《Agent 大厂面试题汇总》](https://notes.kamacoder.com/interview/llm/agent_interview.html) —— 「面试焚诀」段（Harness 概念、OpenClaw / Hermes / Claude Code 三件套、CC 的 hook 实现）是站方观点，但指出的**方向**与其他来源吻合。
- [博客园 itech《面试 AI Agent 工程师会被问什么？40+ 真题》](https://www.cnblogs.com/itech/p/20111938) —— 自媒体整理，题目多为「面试可能问」口吻，出处不明。

## Wisdom (Communities)

- [牛客网 · 面经区](https://www.nowcoder.com/)
  **中文求职面经的主阵地**，本批材料中质量最高的一手记录多数出自这里。用在哪：搜具体公司的面经、发自己的面经换反馈。注意面经质量方差极大，优先看**含追问链**的帖子。
- [linux.do](https://linux.do/)
  技术向社区，面试实录帖的追问链质量高于牛客平均值（发帖人常附背景与录音整理）。用在哪：找非大厂 / 中小公司的真实面试体验 —— 这正是本工作区关心的「不局限于大厂」。
- [GitHub · datawhalechina/hello-agents](https://github.com/datawhalechina/hello-agents)
  开源 Agent 教程 + 面试整理，可参与 issue 讨论。用在哪：把面试问题反哺进开源题库，用「回答问题」来检验自己。
- **未采纳**：B 站视频与微信公众号（内容封闭、无法引用核对）；脉脉（信噪比过低）。

## 两侧的收敛信号（独立调研得出同一条结论）

中文侧与英文侧是**分开检索**的，但两边指向同一个答案：

- **中文侧**：评测是「2026 下半年区分度最高的一簇」，与「一手面经成组出现 + 平台文章标题直指必考 + 岗位旁证」三重信号同时出现。
- **英文侧**：evals 被反复称为 **the differentiator**；take-home 作业里约 20% 直接就是「写一个评估框架」。
- **两边都提成本**：中文侧问「Token 怎么治理、降了多少」，英文侧把 token budget 当作每道 agent 设计题的**一等公民**（不是加分项）。

**对你的含义**：你在 `CharApp/eval` 上的投入是这批语料里最值钱的资产 —— 但要按上面的口径讲。

## Gaps

- ~~框架生态的当前事实~~ —— **已补齐**（2026-10-10 三路调研完成，压成 [`reference/ecosystem-2026-10.html`](reference/ecosystem-2026-10.html)：框架版本与定位、协议层、记忆方案、评测平台与基准、模型侧事实、上下文工程各家做法）。**但仍需练成口述** —— 事实在纸上不等于答得出。
- **JD 旁证（市场在招什么）** —— 本批材料最薄的一块：只有一条未经核验的转述（字节/快手在招评测方向岗位）。若要把「市场要什么」讲实，需要自己去招聘平台拉一批 JD 做词频。
- **同源污染未解决** —— 多份「汇总帖」互相转载同一批面经，「多来源命中」被系统性高估。凡频率判断，本工作区一律标注为「来源数」而非「真实频率」。
- **中文侧的掘金 / B 站 / 公众号未覆盖** —— 抓取能力所限；若某个主题的中文材料显得单薄，先怀疑是覆盖缺口而非真的没人问。
- **时效性事实需要定期复核** —— 框架版本、协议规范、基准榜首都在滚动。这份语料是 2026-10-10 的快照；面试前一周应重新核对 §2 与 §5.3。
