# 26 · 框架侧：压缩的正确性与降级收口（三个逻辑缺陷 + 四项补强 + 帧 v7）

**Status:** done

**Type:** task

**Blocked by:** 无（16 的收口、23 的续篇，两者均已 done）

**上游:** `CharAgent/docs/DESIGN.md` 难点表 **#7 上下文压缩**；`CharApp/docs/PLAN.md` §5（L2.5）；`../PRD.md` §4.9（L2.5 行）

## 做什么

2026-09-23 一轮「大厂做法调研 + 代码审计」挖出来的东西。**调研结论：CharAgent 的压缩在设计上不落后**（账本/视图分离 = DeepAgents 的非破坏性设计、滚动摘要 = LangChain 没有的、切点不拆 tool 配对 = LangChain 会拆的、水位线滞回 = 三家都没有），**落后的是三处实现错误与四处治理缺口**。

三处错误都**已用最小复现跑出来**（见事实表），不是读代码猜的。四处缺口各有大厂的一手做法对应。本片按「故障链」分四批收口：

```
批① 正确性   三个缺陷 + reasoning 计入估算   ← 估的、丢的、截的, 三件事都要对
批② 治理     clear_at_least + 摘要输入上界 + 超限兜底 + reasoning 开关
批③ 帧形状   metadata.view 也走 prompt_ref 引用 (v7) + 两个诊断观察值
批④ 文档     2 篇 ADR + CONTEXT.md 词条 + 面试材料两份 + 本票
```

## 已经替你确认过的事实

### 三个缺陷（复现输出是原样的）

| # | 事实 | 出处 / 复现 |
|---|------|------------|
| 1 | **锚估算器的坐标在源头就不一致**：`note_usage` 标注的坐标是**账本条数**，而那个真实值来自**视图**。复现：锚 = 8000（发出去的视图 4 条的真实量）, `count(同一视图) = 415`（走了「更短列表」守卫退回裸启发式 → **低估 20 倍**）, `count(账本 17 条) = 13291`（锚 + 账本[4:] 估算 → **高估一倍**）, 账本裸启发式只有 6520 | `CharAgent/agent/loop.py:719`（`message_count=len(state.history)`）+ `CharAgent/agent/compaction.py:258-262` |
| 2 | **摘要失败时切点照推，原文凭空消失**：复现（账本 17 条、`summarize=True`、摘要不可用）→ 轮1 `dropped=14 / summary_covers=0 / summary=None / 视图 3 条`，`history[1:15]` 那 14 条**既不在摘要也不在视图里**；轮2 `dropped=16` —— **每轮只看得见最后一个问答，且永远没有摘要** | `compaction.py:430-441`（`summary_covers` 不推进但 `_view(cut=cut)` 照推）；docstring `compaction.py:388` 明写的不变量被破坏 |
| 3 | **摘要被长度截断时静默采用**：`_summarize` 只看正文非空即用，**不读 `finish_reason`**。滚动摘要越长越长，撞上 `summary_max_tokens=1024` 后每轮都是半截，还拿它当下一轮的 `previous` 继续重压 | `compaction.py:590-594`；正文的截断有 CONTINUE/CONDENSE 两套策略，摘要这条路径一个都没有 |

**缺陷 2 的触发条件是活的**：`summarize=True` 且摘要失败（上游超时 / 报错 / **思考吃掉 `max_tokens` 导致正文为空** —— 后者正是 2026-09-23 真机踩过的那个，见 `compaction.py:570-576`）。测试 `test_a_failed_summary_does_not_advance_summary_covers` 只钉了账目那一半（covers），**没钉视图那一半（cut）**。

### 四处缺口

| 缺什么 | 大厂对应 | 现状 |
|--------|---------|------|
| 上游超限**零兜底** | DeepAgents 的 `ContextOverflowError` fallback | 400/413 → `ModelStatusError` → 判「永久错误」不重试 → loop 不捕获 → 直接上抛（CLI 打印失败继续 / server 发 error 终局）。**没有任何「收到超限 → 压一次 → 重发」的路径**；估算偏低 = 这一轮直接失败 |
| `clear_at_least` | Anthropic context editing（官方样例 trigger 30000 / clear_at_least 5000） | 无 —— 可能花一次摘要调用只省几百 token |
| 摘要**输入**无上界 | LangChain `trim_tokens_to_summarize`（默认 4000） | `message_text` 只截 `tool` 角色，user / assistant 正文不截 → 裁掉一大段时摘要请求可能上万 token |
| `reasoning_content` 从不清理 | Anthropic `clear_thinking_20251015` | 无条件回填进账本（[messages.py:89-91](CharAgent/agent/utils/messages.py#L89)，保真 #11），**压缩路径完全不碰它**（`_view` 只改 tool 的 `content`）；被裁的消息其 reasoning 随视图消失、账本里仍在 |

**并且 `reasoning_content` 不计入估算**：`_message_tokens` 只累加 `content` + `tool_calls`。DeepSeek reasoner 的 reasoning 常比正文长数倍 —— 这是与缺陷 1 同类的**正确性**问题。

### 接线与形状（改动时要用的）

| 事实 | 出处 |
|------|------|
| loop 每轮把**全量账本** `state.history` 传进 `apply()`，只把 `compiled.messages` 发给 `generate`；账本一个字不改 | `loop.py:668-699`、`736-777` |
| 投影**每轮都做**，阈值只管「要不要再切一刀」（2026-09-23 ticket 23 修） | `compaction.py:365-411` |
| **帧里的 `state.messages` 与 loop 内存的 `state.history` 不是一回事**：造帧时 `detach_identity` 已把第 0 条身份说明摘掉、只留 `prompt_ref`（帧 v5 起） | 写侧 `loop.py:1096-1099`；读侧 `session.py:523-525`（`restore_identity` 按引用 `load_prompt` 取回正文**并重算引用**）；两侧都幂等 |
| `prompt_ref` 形状：`{"name": "system/v2", "sha256": "..."}`，sha 算的是**渲染后**正文（`${model_name}` 替换之后）；`None` 语义是「这一帧**没剥离过**，正文内联在 `messages[0]` 里」 | `CharAgent/prompt/ref.py:77-96`、`migrations.py:64-69` |
| **`metadata.view.prompt_ref` 与 `state.prompt_ref` 必然恒等** —— 视图第 0 条就是 `history[0]`（同一个对象） | `compaction.py:515`（`view: list[ModelMessage] = [history[0]]`） |
| 摘要那一次调用的 usage **计入**运行预算、**不占** `max_turns` | `loop.py:770-771`（`count_tokens(compiled.summarizer_usage)` + `accumulate_usage`） |
| `view_payload` 的 `same` 判定今天用 `compiled.messages == list(ledger)`（直接比，不是看 `compacted`） | `compaction.py:191-195` |
| CharApp 的压缩装配：`build_compaction_for` 一次给一对（策略 + 估算器），**估算器每会话一份**；`summary_max_tokens` / `summarizer` 业务侧不配（走框架默认 / 主模型） | `CharApp/minimall/service.py:227-255`、`:343-351` |
| 五个旋钮：阈值 32000 / 留 6 轮 / 工具截 2000 / 摘要 on / 水位线 0.7（框架默认是 24000 / 2 / 800 / on / 0.6） | `CharApp/minimall/config.py:64-78`、`.env.example:68-83` |
| **帧不会过期**（可恢复性的前提）：CharApp 走 Postgres，行一直躺在表里；「保留期策略仍属后续阶段」 | `CharAgent/checkpoint/postgres.py:105`、`:222-225` |
| 没有 Anthropic 适配器（只有 DeepSeek OpenAI 兼容端点两个）→ 中间 system 消息**当前**不构成跨家风险 | `CharAgent/model/client_httpx.py:107-125`、`client_sdk.py:144-168` |

### 本轮定下的决策（用户的）

| 问题 | 决定 |
|------|------|
| 边界 | 三个缺陷 + 四项补强；**不做**「可恢复引用 / 模型可 recall / 分层摘要」（那些牵动「记忆」那一层的边界，单开一片） |
| 落点 | **正确性 + 可观测性**（可恢复性用户判断「已有入口」——成立，见上表「帧不会过期」那行） |
| 缺陷 1 修法 | **退化为「启发式 + 固定开销校准」**（不是拆两个方法、不是锚改记视图、不是上分词器） |
| `clear_at_least` | 加，取 **`threshold × 0.1`**（CharApp 32000 → 3200） |
| 降级阶梯 | 摘要失败 → **不切刀**；**超限才允许硬截断**（`emergency()`）；`summarize=False` 语义不变 |
| 摘要治理 | 输入加上界（默认 8000，截**最旧**那端）；输出撞长度 → 当失败处理 |
| reasoning | 计入估算**必做**；清理做成开关 `reasoning_keep_turns`，**默认关** |
| 可观测性 | 帧 metadata 为主，**不进事件**；Dashboard 聚合留到有真实数据之后 |
| 兼容性 | `TokenCounter` 协议 / `CompiledView` 字段 / 帧格式**都可以改**；唯一不动的是**账本语义**（`state.history` append-only + 会话层 summary 契约） |
| 帧的 `prompt_ref` | 与 `state` **同构**：`view.prompt_ref` + `view.messages` 摘掉第 0 条 |

## 具体任务

### 批① 正确性

1. **`AnchorTokenCounter` 退化为「启发式 + 固定开销校准」**
   - `overhead = 真实 input_tokens − estimate(那次真发出去的列表)`，存成常量；`count(任何列表) = estimate(列表) + overhead`。
   - **协议要变**：`TokenCounter.note_usage` 得知道「那次发出去的是哪份」（现在只有一个 `message_count`）—— 调用点 `loop.py:719` 从 `len(state.history)` 改成传**视图**。
   - 附带修掉「退化为纯启发式时漏掉 ~5.7k 固定开销」：overhead 对账本 / 视图 / 任何列表都通用（工具 schema 与 system prompt 两边都在）。
   - **代价要写进 ADR**：失去自校准，比例型偏差（中文实际 token/字）留在估算里；换来的是三处 `count()` 同一口径。
2. **`estimate_tokens` 计入 `reasoning_content`**：`_message_tokens` 加一行（与 `content` / `tool_calls` 同级）。
3. **摘要失败 → 不切刀**：`apply` 里 `summarized=False` 且本轮已定 cut → 返回只投影的 `unchanged`（保住 `compaction.py:388` 那条不变量）。
   - **`summarize=False` 时语义不变**：那时硬截断是**用户选的**，`summary_covers` 直接推进到 cut（账目自洽）。两者要分开判，不是同一个分支。
4. **摘要 `finish_reason == length` → 当失败处理**（走 3 的出口，不另开一支）。**不**靠调大 `summary_max_tokens` 兜——那是治标，且 1024 是按「关思考后装得下」定的。

### 批② 治理

1. **`clear_at_least = threshold × 0.1`**：`_choose_cut` 定了 cut 之后判 `before − 试算视图大小 < clear_at_least` 就不压。它天然给「没压动」多加一道闸（现在只有 `_cut_index` 返回 0 才不压）。
2. **摘要输入上界 `summary_input_limit`（默认 8000）**：截**最旧**那端（保留最近的，因为与当前任务更相关）；**上一条摘要不截**（否则破坏滚动语义）。
3. **超限兜底三层**：
   - 识别：`ModelStatusError` 加 `kind`（`context_overflow` / `invalid_request` / …），判据集中在**适配器**一处（那里拿得到完整错误体）——不靠字符串匹配散在各处。
   - 处置：`CompactionPolicy` 加 `emergency()`（不问水位线，裁到只剩最近 1 个提问）；**不改现有 `apply` 的签名**。
   - 触发：loop 捕获「超限」→ 调 `emergency()` → 重发**一次**（只一次）。
4. **`reasoning_keep_turns`（默认关）**：开了就把 N 轮之外的 assistant 消息 `reasoning_content` 换成占位文本，**不删 key**（保持 wire 形状，免得下游按 key 缺失分支）。

### 批③ 帧形状 + 可观测性（一次升 v7）

1. **`metadata.view.prompt_ref` + `view.messages` 摘掉第 0 条**：格式与 `state` 同构。剥离动作放在**造帧那一侧**（与 `detach_identity` 同一处、同一纪律——`prompt/ref.py:158` 写明「三个后端一视同仁」，放序列化里会让形状随后端而变）。
   - **`prompt_ref` 恒等于 `state.prompt_ref`**（见事实表）；存两份是**用一点冗余换 `view` 自描述**（读的人不必知道「得去 state 里找」）。这个取舍写进字段 docstring。
   - **`view.messages` 不再是「可直接发给模型的完整列表」**——读取时要 `[prompt_ref 取回的正文] + view.messages` 拼回去。这条必须写死，否则以后有人拿它直接喂模型就少了身份说明。
2. **`view_payload` 的 `same` 判定跟着改**：先在未摘状态下比，再摘（抽掉第 0 条之后判据会变成 `view.messages == list(ledger[1:])`）。
3. **`_read_view` 的 `prompt_ref` 复用 `_read_prompt_ref` 的严格校验**（恰好两个键、非空字符串）——`_read_view` 现在的「宽松、不逐字段校验」策略不该延伸到引用上。
4. **v7 迁移**：老帧的 `view.messages` 带身份说明、无 `prompt_ref` → 补 `None`。**与 v5 对 `state` 的迁移完全同构**（v5 就是「prompt_ref 补 None，正文仍在 messages[0] 里」）。
5. **帧 metadata 加两个诊断值**：**估算偏差**（`estimate + overhead` vs 真实 `input_tokens`）、**缓存命中率**（`cache_hit/(hit+miss)`，数据已在 `accumulate_usage` 里）。`clear_at_least` 挡掉的那次记「为什么没压」。
   - 落点是**帧**不是事件：这两个是**诊断值**不是进度值，用户不需要知道「这次估算偏了 3%」。

### 批④ 文档

1. **ADR 两篇**（三条件全过：改了难回退 / 反直觉 / 真取舍）：
   - 「锚估算器退化为启发式 + 固定开销校准」（放弃了什么、为什么那个收益本来就是假的）
   - 「摘要失败不切刀、超限才允许硬截断」（把「允许丢信息」的权力交给一个罕见且明确的触发条件，而不是一次网络抖动）
   - 第三篇候选：帧 v7 的 `view.prompt_ref`（若形状决策够格就写）
2. **`CharApp/CONTEXT.md` 词条**：`clear_at_least`、降级阶梯、帧形状（`view` 与 `state` 同构）、「两个 state.messages 不是一回事」（写侧剥离 / 读侧还原）。
3. **面试材料两份**（放 `.scratch/CharApp/interview/`，用户已建目录）：**题典**（走 `knowledge-interview-prep` skill 的五段格式）+ **专题底稿**（行业四方法 + 本项目逐条映射 + 问答）。内容**以本项目为锚**：知识段落先答行业标准答案，再答「我项目里怎么做的、和标准的差距在哪」。
4. **本票**：开发完成后补「## 实际开发情况」，并勾掉验收框、把 `Status` 改成 `done`。

## 验收

**批①**

- [x] `count()` 对同一份列表在账本路径与视图路径上**给同一个数** —— `test_every_list_shares_one_calibration`
- [x] 注入 `Usage` 后，那一份量出来精确等于上游说的数，别处只差启发式自身的偏差 —— 同上 + `test_the_calibration_absorbs_the_fixed_overhead`
- [x] `reasoning_content` 计入 `estimate_tokens` —— `test_reasoning_content_counts_toward_the_estimate`（差值按**同一把尺子**算，不是只断「更大」）
- [x] 摘要失败（报错 / 无模型 / 正文为空 / `finish_reason=length` 四种）→ **本轮不切刀**，且「视图丢掉的正好是 covers 覆盖过的那段」成立 —— `test_a_failed_summary_falls_back_to_projection_only`（四个参数化）+ `_missing_from_view` 那条不变量断言
- [x] `summarize=False` 时硬截断语义不变（covers 推进到切点，账目自洽）—— `test_switching_the_summary_off_keeps_the_previous_one`
- [x] **真机核过**（2026-09-24）：聊到触发压缩后问「我那个订单的订单号是多少」→ 模型答出 `202607290314520000107906` + 状态「已完成」+ 金额 5098.00 + 完整时间线；页面同一处出现「已压缩更早的 35 条消息, 估算省下约 5.7k token」。**但「断开摘要模型」那一支构造不了** —— 没有能让摘要**单独失败**的 env 开关（`CHARAPP_CONTEXT_SUMMARY=off` 是配置选择，走的不是失败那条路），它由单元测试的四支参数化覆盖

**批②**

- [x] `clear_at_least` 生效：压了只省一点 → **不压**，且载荷里记下原因（`skipped="clear_at_least"`）—— `test_a_saving_below_clear_at_least_skips_the_cut`（另有「省得够多就照压」一条对照）
- [x] 摘要输入被截到上界内，砍的是**最旧**那端 —— `test_the_summary_material_is_capped_from_the_oldest_end`
- [x] 超限兜底：mock 一个 400 `context_overflow` → 自动紧急压缩 + 重发**一次**成功 —— `test_an_overflow_error_triggers_one_emergency_compaction`；「再超不重试」「别的 400 不救」各一条
- [x] `ModelStatusError.kind` 在**适配器侧**可测（`context_overflow` 与 `invalid_request` 分得开）—— `test_http_400_context_overflow_carries_its_kind`（respx 走真适配器）+ parse 层三条
- [x] `reasoning_keep_turns` 默认关时**行为与今天一致**；开了之后 N 轮之外的 reasoning 变占位、**key 仍在** —— `test_reasoning_is_kept_by_default` + `test_reasoning_older_than_the_kept_turns_is_clipped`
- [x] **真机核过**（2026-09-24）：`CHARAPP_CONTEXT_MAX_TOKENS` 临时调到 **6000**（比票面的 12000 小 —— 会话已经聊到 ~6300，重启服务即可看见压缩，不必再灌十几轮），灰字出现两次（35 条 / 40 条），与帧里的 `dropped` 计数对得上

**批③**

- [x] 帧里 `metadata.view.prompt_ref` 与 `state.prompt_ref` **相等**，且 `view.messages` 第 0 条不再是身份说明 —— `test_the_frame_stores_the_view_without_its_identity`
- [x] 按引用把正文拼回去，**逐字等于**当时真发出去的那份 —— 同一条（capturing model 收住了真发出去的那份）
- [x] 读老帧 → `view.prompt_ref` 为 `None`、`view.messages` **一条没少** —— `test_a_v6_view_without_a_ref_reads_back_as_not_detached`（v6 fixture 的 view 是 null，所以另造一个带 view 的 v6 body 走 `migrate_body`）
- [x] `view_payload` 的 `same` 判定在新形状下仍然对（视图 == 账本时 `messages` 为 `None`）—— `test_a_view_identical_to_the_ledger_is_not_copied_into_the_frame`（剥离在**造帧侧**，判据在剥离**前**算）
- [x] 帧 metadata 里能读到估算偏差与缓存命中率 —— `test_the_frame_remembers_how_far_off_the_estimate_was`

**批④**

- [x] ADR 落盘（0011 / 0012 / 0013，含被放弃的方案与代价）、`PLAN` §6.3 十条 → **十三条**
- [x] `CONTEXT.md` 词条落盘（加在「上下文视图」下：压缩降级 / 紧急压缩 / 视图第 0 条与账本同一对象）
- [x] `.scratch/CharApp/interview/` 两份材料落盘（`INTERVIEW.md` 题典 + `context-compaction-notes.md` 底稿）
- [x] `pytest CharAgent` 全绿：**1052 passed, 84 deselected**；`-m "pg or pg_db"`：**70 passed**
- [x] `pytest CharApp` 全绿：**198 passed**
- [x] `ruff check CharApp CharAgent` 干净（`ruff format --check` 也干净：195 文件）

## 备注

### 外部依据（本轮调研的一手来源）

| 来源 | 关键做法 |
|------|---------|
| Anthropic《Effective context engineering for AI agents》 | compaction（Claude Code 95% 触发，保留最近 5 个访问过的文件）+ structured note-taking + sub-agent 三件套；「**先最大化 recall，再优化 precision**」；最轻量的压缩是 **tool result clearing** |
| Anthropic context editing API | `clear_tool_uses_20250919`（trigger / keep / **`clear_at_least`** / exclude_tools）+ `clear_thinking_20251015`（按 thinking_turns 保留最近 N 轮） |
| Manus《Context Engineering for AI Agents》 | KV-cache 命中率是**第一指标**（缓存价差 10x，agent 输入输出比 100:1）；context append-only；**压缩必须可恢复**（丢正文留 URL/路径）；mask don't remove；**保留错误**；recitation 把目标顶到注意力末端 |
| LangChain `SummarizationMiddleware` | keep 默认 20 条、`trim_tokens_to_summarize` 默认 4000、摘要以 AIMessage 放回、`allow_partial=True`（**不保 tool 配对**）、失败降级留最后 15 条 |
| DeepAgents `create_summarization_middleware` | **与 CharAgent 的设计同源**（不碰 `state["messages"]`，摘要记在私有字段）；多三件：淘汰内容**写入后端文件 + 摘要嵌路径 + 可 `read_file` 取回**、摘要前先截大工具参数、`ContextOverflowError` 兜底重试 |
| 面试侧的两套框架 | 四方法（滑动窗口 / 摘要压缩 / 重要性过滤 / 结构化抽取）+ **Prompt Caching 是「计算层」补充不是替代**；Offload / Reduce / Isolate 三策略 |

### 与面试框架的对照（面试材料的骨架）

CharAgent 覆盖到的：**摘要压缩 ✓**（且是滚动摘要，比「压完即丢」强）、**滑动窗口 ✓**（提问为单位、不拆配对）、**工具结果过滤 ✓**（`tool_result_limit`，对应 Anthropic 的 tool clearing，但只截短不清空——因为它还支撑 `truncate_text` 那句「省略 N 字」的可操作提示）、**Prompt Caching 的部分意识 ✓**（ADR-0005 已写明「压缩会让新前缀按未命中价重算一次，压缩买的是窗口余量与首字延迟，不是钱」）。

**没覆盖的**（本片不做，理由是边界）：重要性过滤、结构化抽取、分层摘要、子智能体隔离、可恢复引用（模型按需 recall 原文）——最后一条是最大的差距，单开一片。

### 不在本片

- **`reasoning_content` 的清理默认关**：DeepSeek 契约要求「带 tools 时历史 reasoning 须完整回传」，项目注释记录 2026-09-11 实测 11 组条件**未复现**该 400，但「不复现不等于契约不存在」（`messages.py:77-84` 原话）——开关默认关 = 零风险拿到估算修复。
- **可恢复引用 / 分层摘要 / 重要性过滤**：牵动「记忆」那一层的边界（`compaction.py:51-53` 明写「本模块不做」），单开一片。
- **帧的保留期策略**：`postgres.py:222-225` 明写「仍属后续阶段」。**它一旦做了，本片依赖的「可恢复性」就有窗口了**——那时 `metadata.view` 也需要跟着定保留口径。
- **中间 system 消息的适用范围收窄**：当前只有 DeepSeek 适配器，不是风险；将来接 OpenAI reasoning 模型或 Anthropic 时，`messages.py:11` 那句注释（「OpenAI/DeepSeek 兼容端点均允许中间 system 消息」）要先改。

### 给实现者的两个提醒

1. **不要被两个 `state.messages` 骗到**：loop 内存里的 `state.history` **含**身份说明；落帧后的 `CheckpointState.messages` **不含**（`detach_identity` 摘过）。`_view` 的 `view[0] = history[0]` 前者成立、后者不成立。
2. **缺陷 2 的修法不是「把 covers 也推上去」**：那是把静默丢信息合法化。正确做法是**不切刀**——把「允许丢」的权力留给 `emergency()` 那条罕见且明确的路径。

## 实际开发情况

### 一、四个批次（一次做完）

| 批 | 落地了什么 | 主要文件 |
|---|-----------|---------|
| ① 正确性 | 锚估算器退役 → `CalibratedTokenCounter`（启发式 + 固定开销校准）；`reasoning_content` 计入估算；摘要失败不切刀；摘要被 `length` 截断当失败 | `compaction.py` · `utils/messages.py` · `loop.py` |
| ② 治理 | `clear_at_least`（两道门）；摘要输入上界；超限兜底三层（`ModelErrorKind` + `emergency()` + loop 捕获重发一次）；`reasoning_keep_turns` | `compaction.py` · `utils/messages.py` · `model/parse.py` · `model/utils/errors.py` · 两个适配器 · `loop.py` |
| ③ 帧形状 + 可观测性 | `metadata.view.prompt_ref` + `view.messages` 摘第 0 条；`estimate_drift` / `cache_hit_ratio`；帧 **v7** | `prompt/ref.py` · `compaction.py` · `loop.py` · checkpoint 三个文件 |
| ④ 文档 | 3 篇 ADR + PLAN 索引 + CONTEXT.md 词条 + 面试材料两份 + 本票 | 见 §四 |

### 二、票面没写死、实现时定的（五处）

**1. 触发判据改成量「投影」，不再量账本** —— 本片最重要的一处，票面里没有。

票面只说了「缺陷 1 的修法是退化为校准式」，没提判据。实现时发现**它是必须跟着改的那一半**：原来的滞回靠「锚落到小值」提供（压完那一轮发的是小视图 → 上游回的真实用量小 → 下一轮估算落到线下）。锚一退役这条机制就没了，而账本 append-only 只增不减 —— 拿它判的话**压完一次就永远超线**，每轮都切一刀、每轮都烧一次摘要。改成量投影之后滞回自己就长出来了：投影才是真正要发出去的东西，它因为上一刀已经推过切点而变小。已写进 ADR-0011。

**2. `clear_at_least` 其实有两道门** —— 票面只写了第二道。

第一道是「**省下来必须是正的**」：压完视图里会多出摘要那条（前缀本身就有二三十字），账本小的时候它可能比裁掉的那段还大，此时压了反而更大。这一道**与配置无关**（`clear_at_least_ratio=0` 也挡），而第二道才是「够不够本」。分开写之后，测试的失败信息也说得清了（`saving=-13` 一眼看得出是哪种）。

**3. `compiled_counts` 多了三个键**（`reasoning_cleared` / `skipped` / `emergency`）。

票面只要求「`clear_at_least` 挡掉的那次记下为什么没压」。但「这次做了什么」本来就是**唯一一处定义**（事件与帧共用），清思维链与紧急压缩同样是「做了什么」，各占一个键才不撒谎。事件里 `skipped` 恒为 None（被挡下时不发事件），所以前端契约没变。

**4. 三篇 ADR 而不是两篇** —— 票面说「两篇 + 第三篇候选」。第三篇（帧 v7 的 `view.prompt_ref`）三条条件都过：改了难回退（帧格式落盘）· 无上下文时反直觉（视图里为什么还要摘一次）· 真取舍（存两份引用换自描述 vs 零冗余）。

**5. 骨架重构：`_compile_view` 拆出 `_account` 与 `_emergency_view`。**

超限兜底要在「投影产物的收尾」（落 state / 记预算 / 发事件）上复用同一条路，所以把那三件事抽成 `_account`，两条来路共用；顺带把 `self._model.generate(...)` 那一长串参数抽成 `_generate`（现在有两处调它）。

### 三、测试侧的数据适配（五类）

新加的两道门让**小账本**场景变得压不动（压了反而更大）。这不是 bug，是「压了不划算就别压」在测试的小阈值下变得频繁。受影响的都是**本来只想验别的事**的用例：

| 用例 | 处理 |
|------|------|
| `test_the_kept_question_survives_an_unmatched_call` | 给被裁那一段喂足量内容（够大才省得出来） |
| `test_client_session.py` 三条（会话层接线） | 首轮提问加长（`"第一次提问" + "。" * 60`） |
| `CharApp/tests/test_compaction.py` 的 `ask_twice` | 同上，并把两处 `in` 断言从「列表成员」改成**子串**语义 |
| `test_a_summary_from_the_previous_run_keeps_scrolling` | 新喂的那段加大到 `size=900`（判据是投影，喂得不够压根不压） |
| 六条只验裁剪 / 截断的策略用例 | 显式 `summarize=False`（意图与摘要无关，关掉更纯） |

另有两处**断言语义**跟着改：`test_switching_the_summary_off_keeps_the_previous_one` 从「covers 不动」改成「covers 与切点同进退」—— 旧行为里藏着一条同类振荡（切点推了而 covers 停在旧值，下一轮投影会从旧切点开始，把裁掉的段又放回来）；`test_the_authoritative_usage_is_fed_back_with_what_was_sent` 从「记的是账本条数」改成「记的就是模型收到的那些消息」（`is` 比同一个列表对象）。

还有一处**测试自身的笔误**在开发中被抓到：`all("reasoning_content" in m for m in compiled.messages)` 遍历了**所有**消息（user / system 本来就没这个键），应当只查 assistant —— 实现是对的，断言写错了。

### 四、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `CharAgent/agent/compaction.py` | `AnchorTokenCounter` → `CalibratedTokenCounter`；`apply` 判据改量投影 + 两道门 + 摘要失败不切刀 + 材料上界；`_summarize` 加 `finish_reason` 检查；`_view` 加思维链清理（`trim_old_content` 改名）；`emergency()`；`note_request_diagnostics`；`compiled_counts` 八个键 |
| `CharAgent/agent/utils/messages.py` | `_message_tokens` 计入 reasoning；`cap_summary_material`；`REASONING_CLEARED_TEXT` |
| `CharAgent/agent/loop.py` | `note_usage(sent=view)`；`_generate` / `_account` / `_emergency_view`；`_decide` 捕获超限；`_turn_metadata` 摘视图身份说明 |
| `CharAgent/docs/DESIGN.md` · `client/session.py` | #7 那段与 `counter` 参数说明里的过时说法（「触发靠锚式估算」「降级为纯裁剪 + 事件 warning」）—— 代码审查 #11 抓的 |
| `CharAgent/model/parse.py` · `model/utils/errors.py` | `classify_error` / `ModelErrorKind` / `is_context_overflow` |
| `CharAgent/model/client_httpx.py` · `client_sdk.py` | 构造 `ModelStatusError` 时带上 `kind` |
| `CharAgent/prompt/ref.py` · `prompt/__init__.py` | `detach_view_identity` |
| `CharAgent/checkpoint/utils/types.py` · `utils/migrations.py` · `serialization.py` | `SCHEMA_VERSION=7`；`_v6_to_v7`；`_read_view` 加引用校验 |
| `CharAgent/tests/` | `test_loop_compaction.py`（+15 用例）· `test_model_protocol.py`（+4）· `test_checkpoint_serialization.py`（+4）· `test_model_client_httpx.py`（+1）· `test_client_session.py`（数据适配）· `fixtures/checkpoint_v7.json`（新）· `fixtures/snapshots/*.json`（版本号） |
| `CharApp/minimall/service.py` · `tests/test_compaction.py` | 估算器改名；`ask_twice` 数据适配 |
| `CharApp/CONTEXT.md` · `docs/PLAN.md` · `docs/adr/0011~0013` | 词条 / ADR 索引 / 三篇 ADR |
| `.scratch/CharApp/interview/` | `INTERVIEW.md`（题典）+ `context-compaction-notes.md`（底稿） |

### 五、质量位

| 项 | 结果 |
|---|---|
| `pytest CharAgent` | **1052 passed, 84 deselected**（本片净增 27 条用例） |
| `pytest CharAgent -m "pg or pg_db"` | **70 passed** |
| `pytest CharApp` | **198 passed** |
| `ruff check` / `ruff format --check` | 干净 / 195 文件已格式化 |
| 商城那套（`manage.py test app.minimall`） | **没跑**，按惯例留给你 |

### 六、留给后面的

1. **两条真机验收**（见「验收」里没勾的那两条）。
2. **可恢复引用 / 分层摘要 / 重要性过滤** —— 本片明确不做的那一片（ADR-0012 的末节也点了）。
3. **帧的保留期策略**：`postgres.py` 明写「属后续阶段」。它一旦做了，「可恢复性」就有窗口 —— 那时 `metadata.view` 的保留口径要跟着定。
4. **两个诊断值还没有真实数据**：`estimate_drift` / `cache_hit_ratio` 得跑一段长会话才有分布可看，Dashboard 聚合留到那时。
5. **前端没动**：`reasoning_cleared` / `skipped` / `emergency` 三个新键进了事件载荷，但前端那行灰字的渲染没跟着改（票面没要求）。
6. **`estimate_drift` 在真机上系统性为负**（2026-09-24 首次拿到真实数据，19 帧里 13 帧为负、最大 −3011，而正的那几帧都接近 0：8 / 61 / 94 / 247）—— 按阈值 6000 算，最坏那帧**低估了一半**。方向上是**危险**的那一侧：低估 → 压缩触发得更晚 → 更可能撞窗口（还有 `emergency` 兜底，所以不会硬失败）。这正是 ADR-0011 写明的「校准项随列表大小漂移」，现在有了量：**在视图大小跳变的那些轮可以差一半**。**具体与什么相关还没查**（可能是压缩那一轮的跳变，也可能是中文 reasoning / 工具返回的比例偏差），值得单开一片 —— 候选修法是「只在视图 == 账本时更新校准项」（不被小视图拉低）或「取最近 N 次的中位数」。

### 七、代码审查改了什么（`/code-review`，12 条 finding）

审查跑在修复前的 diff 上，12 条都经过对抗验证。逐条判断后：**9 条修**（其中 4 条是行为缺陷，**都是本片引入的**）· **2 条补文档 / 测试** · **1 条记为已知行为**。

| # | finding | 判断与修法 |
|---|---------|-----------|
| 1 | `summary_input_limit` 削掉的材料「既不在摘要也不在视图」，而 covers 照推 —— **静默丢** | **真**。削材料本身是配置驱动的代价（与 `summarize=False` 的硬截断同类，可认），但**不该静默**。修法**不是**把切点退回去 —— 那会让下一轮的投影把**已经进过摘要**的段又拉回来（covers 与切点分家 = 振荡），正是 `_choose_cut` 要挡住的东西；改成如实记一句 `warning` |
| 2 | `clear_at_least` 的基线取**账本**（含上次已裁掉的段）→ 压过一次之后这道门形同虚设 | **真**。`before` 早就改量投影了，而这一行漏改。修成 `counter.count(projected) - counter.count(trial)` —— 同一把尺子、同一个基线 |
| 3 | 材料为空（`covers == cut`）时整条压缩静默失效 | **部分真**。那一轮确实什么都不做（切点推不动 → 省不下 → 门① 挡），但从前连 `skipped` 都不记。修法是**记原因** |
| 4 | `emergency` 的 `latest_start` 恒等于 `cut`，截断 / 清思维链两个杠杆**恒为 0** | **真**。只剩 1 个提问时两者相等，而紧急路径的典型触发正是「这一轮刚取回一个大工具结果」——那段正文就是保留段的全部。修：紧急时 `latest_start=len(history)`（保护解除） |
| 5 | 超限兜底那一轮发**两条** `context_compacted`，同一个 turn | **不改**。两条都真发生了（第一条描述的那份确实发出去过、只是被上游拒了），且第二条载荷带 `emergency=true` 可区分。写进 `_emergency_view` 的 docstring |
| 6 | 摘要失败时 `replace(unchanged, warning=...)` 把那一次**已计费**的 usage 丢了 | **真**。加 `summarizer_usage=usage` —— 钱花了就是花了，不能因为失败当没发生 |
| 7 | 摘要调用只钉 `thinking=False`，不传 `reasoning_effort` → 实例级默认 effort 会撞上，fail fast 抛错被吞成降级 | **真**（潜在，仓内生产构造点今天没设它）。与 2026-09-23 那次漏 `thinking` 是**同一个坑的两个面**。修：显式传 `THINKING_OFF_EFFORT` |
| 8 | v7 金标用例的 docstring 还写着 v6 fixture；v6 fixture 成了没人读的孤儿 | **真**。改 docstring + 补一条「v6 读得回来」的用例 |
| 9 | `summary_input_limit` 的文档说「<= 0 不截」而校验拒绝 0 | **真**。契约统一成「**0 = 不截**」（校验改成 `< 0` 报错），`cap_summary_material` 那条分支因此可达 |
| 10 | 新公开函数 `detach_view_identity` 没进 `prompt/ref.py` 的 `__all__` | **真**。补上（两个门面的再导出本来就齐，只缺这一处） |
| 11 | `DESIGN.md` #7 段还留着「触发靠**锚式估算**」「摘要失败降级为**纯裁剪**，原因走 `context_compacted` 事件的 `warning`」两处被推翻的说法 | **真**。改（连同 `client/session.py` 的「None 表示用默认的锚式估算」） |
| 12 | `compiled_counts` 的 docstring 说「八个计数」，实际九个 | **真**。改成九个 |

**这批 finding 的共同点**：**没有一条被现有测试照出来** —— 4 条行为缺陷全部落在本片新加的那几个分支上（削材料 / 门的基线 / 材料为空 / 紧急截断），而它们的测试要么**输入落在两种口径恰好相等的那一点**上，要么压根没覆盖那一支。所以每条修复都**补了能复现原缺陷的测试**（新增 5 条，另改 2 条）。

**我自己在修 #1 时先走错了一步**，值得记下来：第一版把切点退到材料起点，让被削的那些「留在视图里」—— 跑测试才发现那会让**下一轮**的投影把已经进过摘要的段又拉回来（covers 与切点分家），视图在相邻两轮之间跳变，正是 ticket 23 修过的同一类振荡。改了第二版（切点不动、代价如实记）才两处都成立。
