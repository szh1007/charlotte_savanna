# C22 · 多智能体原理底稿

**Status:** todo

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

- [ ] 按五段式写成，含 Mermaid 图（至少一张拓扑对比：Supervisor vs P2P）
- [ ] 有「类比」段（建议：**公司里的部门协作**——主管派活（Supervisor）vs 同事互相转交（handoff）；以及「三个人开会讨论一个问题」什么时候比「一个人做完」更慢）
- [ ] 第 6 节的三条实践都**指向本仓的具体文件与行号**
- [ ] 行业侧引用只含一手来源
- [ ] 有一节「**这个项目为什么不做**」，与 `PLAN.md` §5 的结论一致

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
