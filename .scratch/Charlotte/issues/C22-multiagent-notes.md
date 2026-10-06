# C22 · 多智能体原理底稿

**Status:** done（2026-10-06）

**Type:** docs

**Blocked by:** —

**上游:** `CharAgent/docs/DESIGN.md` §4 ⑦ 的 #42/#43/#44；`.scratch/Charlotte/PLAN.md` §5（决定不做实现）

## 为什么要有这份文档（而不是做实现）

**本项目没有多智能体的必要** —— DESIGN ⑦ 那三条（拓扑 / 协作与纠错 / 控制流风险）全是 P2，而 CharApp 的链路里没有一个场景需要「多个 agent 协商」。

**但它是 Agent 岗的高频考点**，而且你手上有别处拿不到的素材：`project/deep_search` 真的用 DeepAgents 跑过 subagent，`project/charplot` 里你还亲手做过一次「**把 subagent 换成确定性检索**」的取舍。所以这一票产出**知识底稿**。

## 落在哪

`.scratch/CharApp/interview/multiagent-notes.md`（与 C21 同一处、同一格式）。

## 格式

按 `INTERVIEW.md` 的五段式（**考点 / 知识点 / 类比 / Mermaid 图 / 口述话术**），三级分层。**行业侧只引一手来源**（Anthropic 的多 agent 工程博客、LangGraph 官方 supervisor/handoff 文档、DeepAgents 官方文档）。

## 必须讲到的内容

1. **拓扑与状态共享是两个独立决策**（DESIGN #42 的原话：别捆在一起选）
   - 拓扑决定「**谁指挥谁**」：Supervisor（中心调度）/ P2P（互相 handoff）/ 层级
   - 状态共享决定「**各自看到什么**」：共享黑板 vs 私有记忆

2. **三条控制流风险**（#44，**必答项**）
   - **子 agent 隔离**：它的失败不能拖垮主 agent
   - **handoff 循环**：A→B→A 要检测并打破（计数 / 指纹 / 上限）
   - **死锁**：多 agent 互相等对方的结果

3. **Critic 模式**（#43）：一个专门的批评者角色，代价是**多一轮调用**，收益取决于任务是否可自检

4. **子 agent 最大的价值是上下文隔离（Isolate）** —— 它烧几万 token，只回一两千字的结论，详细的探索上下文留在它自己的窗口里
   → **这一条与 `INTERVIEW.md` 的 Q3 直接呼应**（那一题讲的 Offload / Reduce / Isolate 三层，Isolate 就是这里）

5. **什么时候不该用多智能体**（**这一条最值钱**）
   - 任务本身是线性的 → 用 workflow（#56）
   - 只是想把 prompt 拆短 → 那是上下文工程，不是多 agent
   - 模型能力不足 → 换模型比加 agent 便宜
   - **加一个 agent = 加一层不确定性、一层通信开销、一条新的失败路径**

6. **本仓的实践素材**（**这份底稿比通用资料值钱的地方**）
   - `project/deep_search/`：DeepAgents 的多 subagent（数据库查询 / 网络搜索 / 知识库）——**用框架跑通的真实例子**
   - `project/charplot/pipeline/stages/search.py:56-58`：**kb 旅程主动绕过 subagent，改用确定性的 `KbSource`** —— 代码注释写明理由是「subagent 是否产出报告不确定」。这是**亲手做过的一次「不用 agent」的取舍**
   - `CharAgent` 为什么不做：DESIGN ⑦ 三条全是 P2，而本项目链路里没有需要协商的场景 —— **记录「不做」与记录「要做」同样重要**

## 验收

- [x] 按五段式写成，含 Mermaid 图（至少一张拓扑对比：Supervisor vs P2P）
      —— 全文 6 张，第一张就是 **Supervisor vs P2P/handoff 的拓扑对比**（两个 subgraph，P2P 那张里直接标出 `Command(goto=…)` 与「这就是 A→B→A 循环的入口」）；余下为：三条控制流风险的形状与闸门 · 子 agent 的 token 账 · Critic 决策 · 该不该上多 agent 的决策树 · 多 agent 评估的两种范式
- [x] 有「类比」段（建议：**公司里的部门协作**——主管派活（Supervisor）vs 同事互相转交（handoff）；以及「三个人开会讨论一个问题」什么时候比「一个人做完」更慢）
      —— Q1 用**公司部门协作**（并补一层：「主管派活 + 所有人盯同一块白板」是坏架构）· Q2 **三个和尚抬水**（隔离 / 画正字 / 放下桶）· Q3 带助手去图书馆做课题 · Q4 论文审稿人 · Q5 **三个人开会**（含「多出来的不是算力，是协调成本」）· Q6 考核项目组别看键盘敲击次数
- [x] 第 6 节的三条实践都**指向本仓的具体文件与行号**
      —— 本票「必须讲到的内容」第 6 条在底稿里**落成 §4**：4.1 `deep_search` 真多 agent（`agent/main_agent.py:19-40`、三个 subagent dict、`:539`/`:567`/`:599` 的库源码锚点）· 4.2 `charplot`「workflow 骨架 + 一格 agent」+ **kb 旅程主动绕过 subagent**（`pipeline/stages/search.py:56-58` 与 `:7-10` 的理由原文）· 4.3 `CharAgent` 为什么不做；合计 20 余处 `文件:行号`
- [x] 行业侧引用只含一手来源
      —— 5 处：Anthropic 两篇工程博客（多 agent 研究系统 / Building effective agents）· DeepAgents 官方文档 subagents 页 · **本项目 `.venv` 里安装的 deepagents 0.7.5 与 langgraph 1.2.9 源码**（逐行可核）。**没抓到的一处如实标注**：LangGraph 官方多 agent/supervisor 页（站点改版、原 URL 404，且抓取额度用尽）→ 改用本地 langgraph 源码讲 handoff 原语与循环兜底，并在底稿里写明「少了官方那份拓扑选型清单」
- [x] 有一节「**这个项目为什么不做**」，与 `PLAN.md` §5 的结论一致
      —— §5，引 §5 表格原话（「本项目没有多智能体的必要。改为写原理底稿（C22）应付面试考察」）并展开成三句；同节列出「已经在的素材」（`deep_search` 真跑过 / `charplot` 活例 / 一次主动放弃）与一句对外口径

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `.scratch/CharApp/interview/multiagent-notes.md` | **新增**（本票交付物，**482 行 / 约 62 KB（UTF-8）**，含评审后修正） |
| `.scratch/CharApp/interview/hitl-approval-notes.md` · `observability-notes.md` · `stateless-and-drain-notes.md` | 首行目录索引改成 **七份**并补上新底稿（工作区里那三行本轮之前是「六份」——其中 hitl / observability 两份的「六份」是 C21 那一轮改的，相对 `HEAD` 则分别是「五份」「四份」）；`stateless-and-drain-notes.md` 那句「本份是六份里**唯一**只讲不做的」改成「本份与 `multiagent-notes.md` 是本目录里两份只讲不做的」（本票加入后它不再唯一） |

> `PLAN.md` §5 那两行的编号错位（多智能体指向 C21、无状态化指向 C20）已在 **C21** 那张票里一并修正为 C22 / C21；本票无需再改。

## 实施记录

**1. 先读码，再写字。** 三个实践素材逐条对着源码核实：`project/deep_search/agent/main_agent.py`（`create_deep_agent` 装配、`subagents` 列表、ContextVar 会话隔离、整轮 try/except）与其三个 subagent dict · `project/charplot/agents/search_agent.py`（**确认它没有 `subagents=` 参数** —— 所以它是「Agent 当节点」，不是拓扑意义上的 subagent，这一点在底稿里单列）· `agents/tools.py` · `pipeline/graph.py` 的四阶段串行图 · `pipeline/stages/search.py:56-58` 与 `:7-10`（kb 旅程绕开 subagent 的代码与理由原文）。另读 `project/deep_search/agent/prompt.py:48` 确认子 agent 的 description/system_prompt 来自配置项。

**2. 行业侧：3 个在线一手 + 2 份本地源码。** Anthropic 两篇博客（原文摘录见 §8 的 A1/A2）与 DeepAgents 官方 subagents 页（A3）为在线抓取；`langgraph` 1.2.9 与 `deepagents` 0.7.5 的引用取自本项目 `.venv` 里安装的那份源码（A4/A5，逐行可核）。**一处没抓到**：`https://docs.langchain.com/oss/python/langgraph/multi-agent` 已 404（站点改版），抓取额度同时用尽 → 底稿在这一条上如实标注「没抓到，改用本地源码」，不拿二手转述补位。

**3. 顺带纠正一个错误记忆。** 写 Q2 时本想写「LangGraph 的 recursion_limit 默认 25」（旧版 LangGraph 的印象），核源码发现 **langgraph 1.2.9 是 `DEFAULT_RECURSION_LIMIT = 10007`**（`_internal/_config.py:32`，可由 env 改）—— 这个数字比「25」更值钱：它说明**图层面那道兜底拦的是死循环，不是拦浪费**，循环检测必须业务自己做。底稿按实测写。

**4. 两个取舍**：① 全文按 `INTERVIEW.md` 的五段式 + 三级分层（高频 3 / 低频 2 / 少数了解 1），每问末尾加「我项目里的做法」；② 把「**subagent 这个词的两层含义**」（DeepAgents 的 subagent 机制 vs 拓扑上的子 agent）单列进 Q1 与 §4.2 —— 这是本仓两份素材分别对应的两种东西，也是这份底稿最容易被问穿的一处。

**5. 交付后跑了一轮两轴评审（Standards / Spec），五处按评审改了：**

| # | 发现 | 修法 |
|---|------|------|
| 1 | §0.3 声称「三个数字都有出处，见 §8」，但 **90.2% 在 §8 里没有原文**（只有 15× 与 10007） | 把 Anthropic 那句 90.2% 原句补进 §8 A1 的摘录 |
| 2 | 「**升级模型比把 token 预算翻倍更划算**」被挂在 A2（Building effective agents）下 —— 逐条核对该篇全文无此句，实际出自 A1（多 agent 博客） | 移到 A1 摘录（连「The latest Claude models act as large efficiency multipliers…」一起），A2 处留一行指路说明 |
| 3 | A3 的 `model` 字段引文末尾「Tasks requiring different model capabilities」实出自同页的 ✅「何时该用」清单，不是该字段描述 | 拆成两行：字段描述按原文，✅ 那条单独标注出处 |
| 4 | §4 五处 `文件:行号` 与工作区不符 | `pipeline/graph.py:24` → **`23`**（`STAGES` 在 23，24 是 `STAGE_PROGRESS`）；`SearchReport` `:30-36` → **`:33-39`**；`build_search_agent` `:38-57` → **`:42-59`**（`create_deep_agent` 调用是 `:53-59`）；ToolStrategy 理由注释 `:50-51` → **`:51-52`**；三个 subagent dict 区间改为 **`network:4-9` / `database:8-13` / `kownledge:7-12`**（原来起点都偏早） |
| 5 | 票面对索引行的描述在「相对 HEAD 看」时对不上（diff 里是五份→七份、四份→七份） | 票面写明两套口径（见「改了哪些文件」那行） |

其余核验全过：`.venv` 里 deepagents / langgraph 的**每一条**行号引用（`subagents.py:252/282/539/547-549/567/595/599`、`_config.py:32`、`types.py:759`、`pregel/main.py:3005`）· deep_search 的 `:19-40`/`:27-31`/`:16`/`:30`/`:86-88`/`:69-79`/`:104-117`/`:119-122` 与 `prompt.py:48` · charplot 的 `search.py:7-10`/`:56-58` · `DESIGN.md:261-274`/`:268`/`:314-325` · `PLAN.md` §5 引文逐字一致 —— 以及「charplot 的 `search_agent.py` **没有** `subagents=` 参数」这条关键断言（由评审独立复核为真）。

**6. 没做的**：没有跑测试（纯文档，不碰代码）；Mermaid 只做语法保守化（节点标签加引号），**未在本机渲染验证**；LangGraph 官方多 agent 页未能取到（见上）。
