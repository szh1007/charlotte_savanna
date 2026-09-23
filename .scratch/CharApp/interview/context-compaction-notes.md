# 上下文压缩 · 专题底稿

> 这一组一共三份：`INTERVIEW.md`（题典：题 + 五段结构）· **本份**（行业一手做法 + 本项目的逐条映射 + 可能被追问的点）· [`estimator-and-trigger.md`](./estimator-and-trigger.md)（原理底稿：token 怎么估、怎么比、压缩怎么被触发 —— 想「学习」就从那一份开始）。
> 来源全部在 2026-09 由 `/mattpocock-skills:grill-with-docs` 那一轮调研抓取，链接在 §1。

---

## 1. 行业全景（一手来源）

### 1.1 面试侧的两套主流框架

| 框架 | 分类 | 关键提醒 |
|------|------|---------|
| 四方法（中文面经里最常引） | 滑动窗口 · 摘要压缩 · 重要性过滤 · 结构化抽取 | **Prompt Caching 是「计算层」补充，不是替代** —— 主动点出这层区别是加分项 |
| Offload / Reduce / Isolate | 卸载（文件系统 / 脚本化 / 渐进披露）· 精简（压缩 / 摘要 / 过滤）· 隔离（子智能体） | 三策略解决三个不同问题：放哪儿、留什么、谁来做 |

### 1.2 企业侧的关键做法

| 来源 | 做法 | 对本项目的意义 |
|------|------|--------------|
| **Anthropic**《Effective context engineering for AI agents》 | compaction（Claude Code 95% 触发，保留最近 5 个访问过的文件）+ structured note-taking + multi-agent 三件套；**「先最大化 recall，再优化 precision」**；最轻量的压缩是 **tool result clearing** | 「先 recall 后 precision」是调摘要 prompt 的方法论；tool result clearing = 我的 `tool_result_limit` |
| **Anthropic context editing API** | `clear_tool_uses_20250919`：`trigger` / `keep`（保几个 tool_uses）/ **`clear_at_least`**（至少清这么多，否则不清）/ `exclude_tools`；`clear_thinking_20251015`（按 `thinking_turns` 保最近 N 轮） | `clear_at_least` 我照做了（取 `threshold × 0.1`）；thinking 清理我做成 `reasoning_keep_turns` 开关（默认关） |
| **Manus**《Context Engineering for AI Agents》 | **KV-cache 命中率是第一指标**（缓存价差 10x，agent 输入输出比 100:1）；context **append-only**；**压缩必须可恢复**（丢正文留 URL/路径）；**mask don't remove**（mask logits 而不是动态增删工具）；**保留错误**（失败留在上下文里，模型才会调整先验）；recitation（todo.md 把目标顶到注意力末端） | append-only 与「压缩必须可恢复」是本项目最缺的两条：前者做到了（账本一字不改），后者只做到「数据还在」而**模型不知道** |
| **LangChain `SummarizationMiddleware`** | `keep` 默认 20 条；`trim_tokens_to_summarize` 默认 **4000**（摘要输入本身有上界）；摘要以 AIMessage 放回；`allow_partial=True`（**会从消息中间切，不保 tool 配对**）；失败降级留最后 15 条 | 「摘要输入有上界」我补上了（`summary_input_limit=8000`）；`allow_partial` 是我**故意不学**的那一条 |
| **DeepAgents `create_summarization_middleware`** | **与 CharAgent 的设计同源**（不碰 `state["messages"]`，摘要记在私有字段）；多三件：淘汰内容**写入后端文件 + 摘要嵌路径 + 可 `read_file` 取回**、摘要前先截大工具参数、**`ContextOverflowError` 兜底重试** | 第三件（超限兜底）是我补上的；第一件（可恢复引用）是我明确没做的那片 |

### 1.3 一句话总结这家务的走向

**「压掉的东西能不能取回来」是分水岭。** 一般实现只做到「省了上下文」，Manus / DeepAgents / Anthropic 更进一步做到「省了但取得到」。后者才是生产级的默认答案。

---

## 2. 本项目（CharAgent）的逐条映射

### 2.1 已经有、而且比通用实现强的

| 能力 | 对照 |
|------|------|
| **账本 / 视图分离** | = DeepAgents 的 non-mutating 设计（不碰 `state["messages"]`）。2026 年的主流做法 |
| **滚动摘要**（上一条摘要连新裁段一起重压） | LangChain **没有**（压完即丢，早期信息逐次稀释） |
| **切点只落在提问处、绝不拆 tool 配对** | LangChain 用 `allow_partial=True`，**会**拆 |
| **水位线滞回** | 三家都没有（DeepAgents 靠 fraction 默认值近似）；而且我的滞回**来自「判据量投影」**，不依赖估算技巧 |
| **帧里记「这一轮真发出去的视图」** | 可审计性：多数生产系统答不出「当时它看到了什么」 |
| **压缩事件 + 帧里的诊断值** | `estimate_drift`（估算偏差）+ `cache_hit_ratio`（缓存命中率）—— 后者直接对应 Manus 那条「第一指标」 |

### 2.2 缺的（按差距排序）

| 缺什么 | 谁有 | 后果 / 为什么没做 |
|--------|------|-----------------|
| **可恢复引用**（摘要里嵌「原文在哪」+ 模型可 recall） | Manus / DeepAgents / Anthropic memory tool | 最大的差距：数据都在（快照 append-only），但**模型不知道能取**。牵动「记忆」那一层的边界（`compaction.py` 明写「本模块不做」），单开一片 |
| **分层摘要**（近期原文 / 中期摘要 / 长期摘要） | 面试框架里的进阶答案 | 只做了一层；做分层要动 `summary_covers` 的账目结构 |
| **重要性过滤 / 结构化抽取** | 面试四方法的后两个 | 属于「记忆」层，不属于「单请求治理」 |
| **子智能体隔离** | Anthropic multi-agent / DeepAgents | 在另一个子项目里做了（`project/deep_search`） |
| **主动压缩**（读完大结果立刻压要点） | 面试框架里的 proactive compression | 只有静态的 `tool_result_limit`；主动压缩要模型自己判断，属于工具设计那一层 |

### 2.3 三处「我修过的真缺陷」（面试上最值钱的部分）

| 缺陷 | 症状（最小复现） | 修法 | 记录 |
|------|----------------|------|------|
| **视图每隔一轮失效** | 八轮模拟 2 压 3 跳、4 压 5 跳 | 「投影」与「切刀」分家 | ticket 23 / ADR-0008 |
| **估算器坐标错位** | 同一份视图 415 vs 真实 8000；账本 13291 vs 启发式 6520 | 锚式 → 校准式；判据从账本改成投影 | ticket 26 批① / ADR-0011 |
| **「降级为纯裁剪」静默吞对话** | `dropped=14` 而 `covers=0`，14 条既不在摘要也不在视图 | 摘要失败不切刀；超限才允许硬截断 | ticket 26 批① / ADR-0012 |

**共同点**（这是可以升华的一句话）：三处都是**「账目与视图各走各的」**—— 账目对了，视图错了，而没有任何一处把两者的差记下来。所以批③ 加了 `estimate_drift` 与 `cache_hit_ratio` 这两个诊断值，让「估得准不准」这件事从此答得出来。

---

## 3. 可能被追问的点

**Q：为什么不直接上 tiktoken 之类的真分词器？**
A：项目里的取舍写在 `utils/messages.py`：中文分词器算中文同样不准，多一个依赖只换来假精度。而且估算只用来**判阈值**，权威值永远是上游返回的 `input_tokens`——我用它反推一个校准项（`overhead = 真实 − 估算`），比换分词器便宜得多。真接上分词器时，把 `overhead` 记 0 即可，协议与调用点都不用动。

**Q：压缩会不会让模型变笨？**
A：会，如果压得太狠。Anthropic 的原话是「过度激进的压缩会丢掉那些重要性后来才显现的微妙上下文」。所以我的默认是**先最大化 recall 再优化 precision**（摘要指令里写死「保留什么、丢掉什么」），并且有一条硬不变量兜底：投影只丢**摘要已经覆盖过**的那段。另外 `clear_at_least` 挡掉「省得不够本」的压缩——花一次摘要调用只省几百 token，不值当。

**Q：如果上游根本不报 `context_overflow` 这种语义错误码呢？**
A：那我就要靠字符串匹配，这是这份设计里唯一一处「猜」。判据写死在解析层一处（`parse.classify_error`），加一种上游写法就是加一行；认不出来时 `kind` 记 `unknown`，行为退化成「照旧上抛」—— 保守的那一侧。

**Q：为什么不干脆用超大窗口的模型？**
A：窗口变大不解决两个问题：**成本**（每轮都要重发全量，线性上涨）与**注意力**（Anthropic 引的研究叫 context rot：token 越多，模型从那堆上下文里准确回忆的能力越差）。所以「等窗口变大就不用管了」这个想法在两头都不成立。

**Q：你这套与 LangGraph / LangChain 的中间件比，优势在哪？**
A：三点。**一，账本一字不改**——LangChain 的 `SummarizationMiddleware` 在 `before_model` 里用 `RemoveMessage(REMOVE_ALL_MESSAGES)` 重写 `state["messages"]`，历史就此不可回溯；我这边压缩只影响这一次请求的输入。**二，不拆 tool 配对**——它用 `allow_partial=True`，我把它当硬不变量。**三，滞回与门槛**——我有水位线与 `clear_at_least`，它没有。反过来说我缺它有的那样东西：**后端 offload**（DeepAgents 那版会把淘汰的消息写进文件），我没做，那是下一片的事。

**Q：摘要 prompt 怎么调？**
A：Anthropic 给的方法是**先最大化 recall，再优化 precision**：一开始让摘要什么都留（宁可啰嗦），在复杂 agent trace 上跑，看哪些信息真的被后续用到了，再逐步删冗余。我的摘要指令里明确列了两组：保留「用户目标与约束 / 已确认的事实（订单号、金额、结论、时间）/ 做过的决定与原因 / 还没做完的事」；丢掉「寒暄与重复表述 / 工具返回里的格式噪音」。另外摘要是**滚动**的——上一条摘要要连新裁掉的段一起重压，否则更早的信息会被逐次稀释到消失。

**Q：跳过摘要那一步，只裁剪行不行？**
A：行，而且我有一个 `summarize` 开关专门给这件事（CharApp 的 `CHARAPP_CONTEXT_SUMMARY`）。关掉之后压缩这一步**一次模型调用都不发**，纯裁剪 + 工具截断。这不是降级——摘要只为「压得更狠」而存在，它每压一次就要花一次钱，花不花由业务按自己的账单决定。注意与「摘要失败」区分：那是**意外**，我选择不切刀（见 ADR-0012）。

---

## 4. 一页速记（面试前扫一眼）

```
压缩的四种方法    滑动窗口 / 摘要 / 重要性过滤 / 结构化抽取
                  + Prompt Caching 是计算层, 不是替代
生产三策略        Offload(文件系统) / Reduce(压缩+摘要+过滤) / Isolate(子智能体)
三个硬约束        前缀稳定(缓存) · 配对不拆(tool_calls) · 至少留 1 个提问
三条不变量        丢了必须进摘要 · system 不裁 · 切点只落提问处
三个数字          缓存价差 10x · agent 输入输出比 100:1 · Claude Code 95% 触发
一句心法          压掉的东西能不能取回来 —— 这是分水岭
```
