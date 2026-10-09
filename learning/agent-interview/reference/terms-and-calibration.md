# 术语与口径 · 话术底稿

> **这份底稿解决一个具体问题**：你的项目里有答案，但答出来的话不在 2026 的坐标系里。
> 每条的组织形式固定为「**行业词 → 一句话定义 → 你的对应物 → 口述话术**」——可直接背。

**建稿依据**：首课自测（2026-10-10）暴露的卡点，集中在三类「答案在项目外面」的题：
口述 1（harness 开场）/ 口述 2（业务数字口径）/ 口述 4（上下文腐化用 2026 的词）。
来自自己项目的两张卡（为什么手搓 / 演示路径）答稳了 —— 所以要背的是**外部概念**，不是项目事实。

**行业侧引注纪律**：本文对行业的每一句断言都附一手来源 URL；凡未核到原文的，标注「未验证」。

---

## 0. 五分钟版

### 0.1 一句话

面试官用行业词提问时，你要能立刻接上「**哦，我做的就是这个**」——本文就是把你的东西逐条挂上行业词的对照表。

### 0.2 三个词撑起你的开场

1. **Harness** —— 「我做的是 harness 层：模型之外的循环、上下文、权限、可观测。」
2. **Context engineering** —— 「上下文是递减边际收益的有限资源，所以我的压缩触发判据量的是**投影**而不是账本。」
3. **口径** —— 「凡报数字我都带被测对象、样本量、跑几次」——这一条不是术语，是纪律。

---

## 1. Harness 体系

### 1.1 术语

| 行业词 | 一句话定义 | 一手出处 | 你的对应物 |
|---|---|---|---|
| **Harness** | 模型之外的**全部工程装置**：循环、工具、状态与上下文管理、权限、流式 | Mitchell Hashimoto《My AI Adoption Journey》2026-02-05（提出 "engineer the harness"）· OpenAI《Harness engineering》 | **CharAgent 整体**：循环 + 九类事件 + 快照 + HITL + 脱敏 + 工具协议 |
| **Agent = Model + Harness** | 把「模型能力」与「工程能力」明确切开的等式 | 同上 | 你的 `pyproject.toml` 只 8 项依赖 —— 模型接口是最薄的一层 |
| **Session / Harness / Sandbox 三接口** | 服务端把 agent 基础设施拆成三个可独立重建的接口：session（append-only 事件日志）/ harness（跑循环的壳）/ sandbox（执行环境） | Anthropic《Scaling Managed Agents》 | `db/`（记录层）+ `agent/loop.py` + 工具执行环境 |
| **pets vs cattle** | 会话与沙箱应可随时重建，不能当有状态宠物养 | 同上 | 三实现快照 + 断点续跑 = 会话可重建 |
| **Harness 之争（2026 叙事）** | 框架之争已转化为 harness 之争：各家卖的是循环 + 上下文 + 权限 + 可观测，而非纯编排库 | LangChain 官方分层：Deep Agents = *batteries-included harness* / LangChain = *customizable harness* / LangGraph = *low-level orchestration* | **你的手写运行时在这个叙事里是正资产** |

### 1.2 口述话术（口述 1 的答案）

> 「我做的是 **harness 层** —— 从一个 agent runtime 的完整工程装置入手，模型接口是其中最薄的一层。
> 这个划分的好处很实际：**模型换代时我的工作量接近零**。
>
> 具体讲三处能证明『不是调包』的地方：agent loop 是**九类事件的状态机**，带四条硬不变量，违反就抛错；
> `finish_reason` 六态分流，截断走续写、上游中断绝不当答案返回；三种软限制（轮数 / token 预算 / 墙钟）加 kill switch 双层停机。
>
> 规模上 1739 个用例、31 条 ADR 记每项取舍的代价与重评条件；真机数字是增量渲染把长答复首字从 16.0s 压到 1.4s。」

**为什么这样开场**：一句话把自己放进坐标系，后面所有细节会被自动归位。反过来说「我手写了一个框架」，面试官先怀疑轮子质量，你得花更多力气爬回来。

### 1.3 追问预案

| 追问 | 答法要点 |
|---|---|
| 「Harness 和 Runtime 有什么区别？」 | 同义层，但 **harness 更强调「模型被套在里面」** —— 这个说法把模型当发动机、工程当车架。Runtime 偏中性，harness 带「承载与约束」的意味 |
| 「手写 harness 和用框架，什么时候正收益？」 | 控制力 vs 开发速度。你的判据：**需要循环语义与事件不变量完全可控**时手写；**产品线要赶交付**时用框架（charplot 就是后者的证据） |
| 「这套东西随模型换代真的不用改？」 | 要诚实：**大部分不用改**，但 `DESIGN #68` 记了一处反例 —— 思考模式的采样约束（`temperature` 被忽略、`top_p` 下限 0.95、思维链与正文共享 `max_tokens` 配额）是**模型侧行为**，换代要重测 |

---

## 2. 上下文工程术语链

### 2.1 术语

| 行业词 | 一句话定义 | 一手出处 | 你的对应物 |
|---|---|---|---|
| **Context rot** | token 越多、召回越差 —— 上下文是**递减边际收益**的有限资源；根因是 n² 注意力与训练分布偏好短序列 | Anthropic《Effective context engineering for AI agents》 | 你压缩触发判据量**投影**而不是账本，正是因为这个 |
| **Compaction** | 摘要压缩，最轻的形态是 tool result clearing | 同上 | 滚动摘要（`summary_covers` / `dropped` 两个字段分别记**位置**与**动作**） |
| **Context Anxiety** | 模型在上下文**将满时过早收尾、敷衍了事** | Anthropic《Scaling Managed Agents》（记 Sonnet 4.5 明显、Opus 4.5 消失） | 你的 `finish_reason` 六态分流接住了这个信号 |
| **Context Reset** | 压缩不够时干脆重置上下文，把状态外化到外部存储 | 同上 + Anthropic《Effective harnesses for long-running agents》 | **ADR-0012「摘要失败不切刀，只有上游报超窗口才允许硬裁」= context reset 的判据化版本** |
| **Progressive disclosure** | 能力 / 指令不一次性全塞进上下文，按需分层加载 | Anthropic 同上 + LangChain context engineering | `DESIGN #51` 的三级渐进披露 + 三级路由 |
| **JIT retrieval** | 从「预推理 embedding 检索」转向「用到时才检索」（glob/grep 式） | Anthropic 同上；Claude Agent SDK 文章 | 工具按需挂载的按场景裁剪（但你的 A/B 结论是**按场景裁剪省的是窗口余量，钱几乎没变** —— 见 §5） |
| **Context quarantine** | 子 agent 自带上下文、只回传结论的隔离机制；两种模式 **isolate**（默认）/ **fork**（复制父上下文） | LangChain Deep Agents subagents 文档 | `project/deep_search` 的 Supervisor + 私有记忆 |
| **KV-cache 命中率是第一指标** | 长前缀下缓存命中与否可造成 10x 成本差；配套纪律「mask, don't remove」（工具变更时遮蔽而非删除，保住 cache 前缀） | Manus《Context Engineering for AI Agents》 | 你底稿 `01-context-compaction.md` Q4 已有同一结论 —— **缺的只是这个外部背书** |

### 2.2 口述话术（口述 4 的答案）

> 「上下文治理业界一般是三级路径：**先 compaction，不够再 context reset**，把状态外化到外部存储 ——
> Anthropic 在长运行 harness 那篇里讲的就是这条。背后的原因是 **context rot**：上下文是递减边际收益的有限资源，
> 不是越长越好。
>
> 我做的是**账本 / 视图分离**：历史 append-only 永不改写，每次调用前把账本投影成『system + 滚动摘要 + 最近 N 个提问』的视图。
> 和主流做法的关键差别在**触发条件** —— 我的设计是**摘要失败不切刀，只有上游报超窗口才允许硬裁**。
> 也就是把 context reset 从『兜底动作』变成一条**有判据的规则**。这条的代价是压缩压力更大，收益是绝不因为自己的判断失误丢历史。
>
> 真机上抓出过两个静态读码看不出的缺陷：投影与切刀合并会让视图隔轮失效、摘要白烧一半；
> 估算器坐标不一致会按账本高估一倍、按视图低估 20 倍。」

**为什么先给行业路径**：不加第一段，面试官听到的是一套自创方案；加了之后你的方案有了坐标 —— 你是三级路径里第二级的强化版。

### 2.3 一处可以主动指出的行业观察（加分）

> 「还有一点值得说：Anthropic 自己记录过，**context anxiety 在 Sonnet 4.5 上明显，到 Opus 4.5 就消失了，
> 于是 reset 反而成了负担**。这说明**工程手段会随模型换代失效** —— 所以我的 ADR 每条都写重评条件，而不是写死结论。」

---

### 2.4 四个易忘词 · 记忆钩子（第 2 课实测：这四个没答上）

> 散着背记不牢。下面把每个词挂到一个**已有的东西**上。

**① Context Anxiety —— 挂到因果链上**

三个词是一条链，一起记：**rot 是机制 → anxiety 是症状 → reset 是处方**。

> 「上下文越多注意力越稀释（**rot**），于是模型快满时开始敷衍（**anxiety**），
> 处方是压缩、不够就重置（**reset**）。而且这条链会随模型换代变化 ——
> Sonnet 4.5 上焦虑明显、Opus 4.5 上消失，**处方反而成了负担**。」

**② Context quarantine —— 用词根记**

quarantine = **检疫隔离**。子 agent 出去跑了一圈回来，**要在门口隔离** ——
只带回结论，不带回它路上积累的几万 token。

> 一句话：「子 agent 探索数万 token，只回传 1,000–2,000 token 的蒸馏摘要。」
> 两种模式：**isolate**（默认，只给一句任务）/ **fork**（继承父对话）。

**③ pass@k vs pass^k —— 看符号本身**

- `@k` 读作「在 k 次机会里**至少中一次**」→ 测**能力上限**
- `^k` 读作「k 次**全都得中**」→ 测**可靠性**
- **同一份数据上两者可以给出相反结论** —— 这就是为什么必须说清是哪个

> 挂到你的做法上：**你每题跑 3 次取分布，走的是 `pass^k` 那一侧**。
> 顺带一句能加分的：Agent 场景更该看 `pass^k`，因为多步任务的错误是**复合**的
> —— 单步 95% 正确、20 步后只剩约 36%。

**④ trajectory eval vs outcome eval —— 用一句话分**

**轨迹答「怎么走的」，结果答「到没到」。**

> 分工口径（可直接背）：**结果做回归门禁，轨迹做归因。**
> 你的对应物：`judges.py` 里工具选择与参数正确率属**轨迹**；回答合规与 markdown 格式属**结果**。

---

## 3. 评测词汇（下一课主题，这里先立骨架）

### 3.1 术语

| 行业词 | 一句话定义 | 一手出处 | 你的对应物 |
|---|---|---|---|
| **task / trial / grader / transcript / harness** | 评测的标准词汇表：任务 / 一次跑 / 判分器 / 完整记录 / 被测装置 | Anthropic《Demystifying evals for AI agents》2026-01-09 | golden.py 的题 / 一次跑批 / `judges.py` / 报告里的逐题表 / CharApp 全套 |
| **pass@k（能力上限）vs pass^k（可靠性）** | 前者问「给 k 次机会能不能做对一次」，后者问「k 次都能做对吗」—— **同一模型的结论可能相反** | 同上 | 你的跑批**每题跑 3 次取分布** —— 这正是 pass^k 的思路，缺的是名字 |
| **Trajectory eval vs outcome eval** | 过程评估 vs 结果评估 | LangSmith trajectory evals 文档 | `judges.py` 的工具选择 / 参数正确率 = 轨迹判据；答复合规 = 结果判据 |
| **Capability evals vs regression evals** | 探能力边界 vs 防回归 | Anthropic 同上 | 两组 A/B 是 capability；同批题重跑是 regression |
| **LLM-as-judge 三偏差** | position bias / verbosity bias / self-enhancement bias；缓解 = pairwise + 位置轮换 + 长度控制 + 结构化 rubric | MT-Bench 论文 arXiv 2306.05685 | **你刻意不做 judge** —— ADR-0021 的论证要接住这三点 |

### 3.2 一句话口径（把 `judges.py` 摆进坐标系）

> 「我的判据是**纯函数**的 —— 不碰网络、不碰库、不看时钟。这样同一份事实喂两次结论一定一样，
> 『这次比上次好』这句话本身就可复现。**LLM-as-judge 我刻意不用**：
> 它有三种已知偏差（位置、长度、自我偏好），而我要证的两件事都能规则化，引 judge 是把非确定性引进**判据本身**。」
>
> 「边界我也清楚：语气、说服力、多轮策略这类过程质量，这套判据量不了。」

---

## 4. 框架对比术语（对比题专用）

> 对比题的答法固定为三段：**对方是什么 → 我的是什么 → 差异的代价在哪**。

| 对比对象 | 对方 | 你的 | 差异的话术 |
|---|---|---|---|
| **HITL 挂起** | LangGraph `interrupt()` + `Command(resume=…)`，状态由 **checkpointer** 恢复 | `POST /runs/{id}/resume` + 三列幂等键 + **挂起不建审批表** | 「机制同构，差异在**幂等**：上游每轮从 `call_0` 重新编号，所以我的键是三列 `(run_id, message_id, tool_call_id)`，少一列必撞。真机战绩是恢复请求重放两次，一分钱没多扣」 |
| **状态存储** | checkpointer（**thread 级短期**）vs Store（**跨 thread 长期**），两个概念分离 | 快照（每 Turn 一帧）vs `charagent_memories`（跨会话长期记忆） | 「**同一个区分**，只是我用了不同的名字。分开的理由是它们回答不同问题：快照答『这次跑到哪了』，长期记忆答『这个人是谁』」 |
| **子代理编排** | `Agent.as_tool()` 保留控制权 vs `handoff()` 移交控制权 | `ToolProvider` 接缝；多 agent 是设计储备未做 | 「OpenAI 那个区分很实用：**控制权在不在原地**。我的工具接缝属于前者；多 agent 我判过、没做，触发条件是……」 |
| **上下文隔离** | Deep Agents 的 isolate / fork | 私有记忆（deep_search 的 Supervisor） | 「我用的就是 isolate —— 子 agent 只收到一句任务描述。代价是**父看不到中间过程**，所以结论必须自带证据」 |
| **改了代码旧会话怎么办** | LangGraph 官方口径：**最新图定义会立即作用于所有已存在的 thread 状态** | 快照按 `thread_id` 分区，水合读回历史、**绝不重放工具调用** | 「LangGraph 这条我要小心 —— 图改了会重新解释旧状态。我的设计是快照只做读回、不做重放，改动的影响面因此可预测」 |

**⚠️ 一条提醒**：这张表里的「对方」全部转述自官方文档（见 `ecosystem-2026-10.html` 的来源），**你最好自己打开核对一遍再背** —— 转述出错比不知道更糟。

---

## 5. 业务数字口径（口述 2 的答案）

### 5.1 三类问法与对应答法

个人项目必然没有真实用户量，但**面试官问数字的真正意图是「你有没有度量意识」**。三种问法分别答：

| 问法 | 陷阱 | 答法 |
|---|---|---|
| 「多少人用 / 日活多少」 | 编一个数 → 面试官一旦发现一个数字是编的，会怀疑所有数字 | 承认性质 → 给**等价的量化替代** |
| 「Token 月消耗多少 / 降了多少成本」 | 报降幅不报口径 | 给**口径 + 绝对量 + 归因方式** |
| 「优化了多少 / 怎么测的」 | 只报结论 | **先给被测对象与样本量**，再给数字 |

### 5.2 口述话术

> **（多少人用）**「这是我自建的求职作品，没有对外的用户量 —— 我不编这个数。
> 但我有等价的评测规模：离线评估 20 题 6 场景、两组 A/B，每项跑 3 次取分布；要验证行为就重跑同一批题。」
>
> **（成本）**「成本我按真机账算：金额在收尾那一刻按峰谷价算好写死，逐模型归因 ——
> 所以我随时能说出某一次 run 花了多少、花在哪个模型上。
> 做过一次工具数量 A/B 的实测结论也比较反直觉：**裁剪工具省下的是窗口余量，钱几乎没变** ——
> token 确实降了 35~38%，但缓存让长前缀近乎免费，所以账单没什么变化。」
>
> **（优化了多少）**「先说被测对象：长答复场景，同一批问题，前后各跑若干次。
> 在这个前提下，增量渲染把『答复区开始出字』从 16.0s 压到 1.4s。
> 但我要补一句边界：**检索类题目首字差别很小** —— 那几秒都花在检索上，不在渲染上。」

### 5.3 一条纪律（判断力题失分点）

**任何数字都带三样**：被测对象是什么、样本多少、跑几次。

反例（最容易被追到底）：「优化了 40%，这是真实环境跑出来的。」
正例：「同一批 20 道题、每题跑 3 次，工具选择准确率从 A 到 B —— 但这个差异落在臂内极差以内，所以我的结论是**这批题上没测出差异**。」

> 最后这个「**没测出差异也照实报**」的答法比任何涨幅都值钱 —— 它证明你会读自己的数据。

---

## 6. 出处清单

**一手（厂商官方 / 论文）**

- Mitchell Hashimoto《My AI Adoption Journey》 https://mitchellh.com/writing/my-ai-adoption-journey
- OpenAI《Harness engineering》 https://openai.com/index/harness-engineering/
- Anthropic《Scaling Managed Agents: Decoupling the brain from the hands》 https://www.anthropic.com/engineering/managed-agents
- Anthropic《Effective context engineering for AI agents》 https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic《Effective harnesses for long-running agents》 https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic《Demystifying evals for AI agents》 https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic《Building effective agents》 https://www.anthropic.com/engineering/building-effective-agents
- Claude Agent SDK 文章 https://claude.com/resources/articles/building-agents-with-the-claude-agent-sdk
- LangChain 官方分层口径 https://docs.langchain.com/oss/python/langchain/overview
- LangGraph《Workflows and agents》 https://docs.langchain.com/oss/python/langgraph/workflows-agents
- Deep Agents《subagents》 https://docs.langchain.com/oss/python/deepagents/subagents
- LangSmith trajectory evals https://docs.langchain.com/langsmith/trajectory-evals
- OpenAI Agents SDK《Agent orchestration》 https://openai.github.io/openai-agents-python/multi_agent/
- Manus《Context Engineering for AI Agents》 https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- MT-Bench《Judging LLM-as-a-Judge》 https://arxiv.org/abs/2306.05685

**本项目（引用的是行号或 ADR，不是转述）**

- ADR-0012（摘要失败不切刀）· ADR-0014（挂起不建审批表）· ADR-0018（成本在收尾写死）· ADR-0019（日志先脱敏）· ADR-0021（不做 judge）· ADR-0023/0024（工具超时）· ADR-0025（熔断嵌重试）· ADR-0029（吐过字不重发）· ADR-0031（MCP 重名 fail fast）
- `CharApp/eval/judges.py`（纯函数判据 + 口径推导）
- `CharAgent/docs/DESIGN.md`（#51 渐进披露 / #68 采样约束 / #58-60 评估与迭代）
- `.scratch/CharApp/interview/` 五份专题底稿

---

> **最后一条使用建议**：这份底稿的用法不是「读」，是**盖住右半边、看行业词说自己的对应物**。
> 配套的反查训练在课时里：`lessons/0002-terms-retrieval-drill.html`。
