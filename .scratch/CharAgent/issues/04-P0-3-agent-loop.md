# 04-P0-3 — agent loop 核心（并行工具 / 错误自纠错 / 循环防护 / length 截断）

**What to build:** 手写 agent loop（while 循环：模型决策 → 并行工具执行 → tool_result 消息回填 → 下一轮）。一个 assistant 消息的多个 tool_call 并发执行（gather + return_exceptions），结果以 tool_result 消息整体回填保持并行语义；可操作错误回填模型二次调用实现自纠错；LoopGuard（max_turns / token 预算 / wall-clock + kill switch 即时打断）防无限循环；finish_reason=length 走截断处理（续写或精简）。每 Turn 结束产生完整消息历史（供 checkpoint 落盘）。

**Blocked by:** 01, 03

**Status:** done

- [x] 单工具调用路径：调用 → 回填 → 模型二次决策正确（#1）
- [x] 并行工具：同一 assistant 消息多 tool_call 并发执行（时间戳验证非串行）；部分失败时成功结果与失败原因一起回填（#1）
- [x] 错误自纠错：工具报可操作错误 → 回填 → 模型二次调用成功（#2）
- [x] 循环防护：max_turns / token 预算 / wall-clock 三种触发点；kill switch 即时打断（#3）
- [x] length 截断处理：续写或精简路径（#10）
- [x] 轨迹断言测试：工具调用顺序与参数可断言（#62）

## Comments

**2026-09-10 实施完成**（提交前 code-review 双轴审查 + 修复；结构后调整为 agent 包门面惯例 + utils 子包）：实现位于 `CharAgent/agent/`——`__init__.py` 纯门面导出、`loop.py`（AgentLoop 手写 while 循环行为主体）、`guard.py`（LoopGuard 三软限制）、`utils/`（errors 错误族 + types 共享类型 + messages wire 消息构造与截断指令文案）。

- **主循环**（AgentLoop.run）：while 循环 = 模型决策 → 按 finish_reason 分支（tool_calls 优先 / length 截断 / 自然终止）→ 每轮结束记录完整消息历史快照。assistant(tool_calls) 消息先入历史再整体批量回填 tool 消息，保证配对结构（#10）；tool 回填按 tool_calls 原顺序（gather 保序），**非完成顺序**——历史稳定可重放、并行语义不退化（#1）；失败工具回填的是 execute_tool 的可操作错误文本，即自纠错载体（#2，错误进历史不进 traceback）；reasoning 回填 wire 历史（#11 + DeepSeek 思考模式契约，详见下方「2026-09-11 提交后补强」§1），但不混入 content 字段，前端折叠展示归 reasoning 事件。
- **并行执行**：asyncio.gather + return_exceptions（单条失败不拖垮整体）；防御分支包「子任务被独立取消」等非预期异常为可操作失败结果；父 task cancel 时 gather 取消全部子任务并传播 CancelledError（kill switch 即时打断，不吞）。
- **LoopGuard**（三软限制）：max_turns / token 预算（usage 累计，无 usage 计 0）/ wall-clock（time_source 注入缝）；**软限制「这一轮结束后才判断」**——已发出的工具调用总执行完回填，历史恒合法，`check_after_turn` 在下一轮模型调用前判定；触发点经 outcome 上报（MAX_TURNS / TOKEN_BUDGET / TIME_LIMIT）。kill switch 不在 guard 内：外部 `asyncio.Task.cancel`，测试证明 <2s 打断 30s 慢工具且零任务泄漏。
- **length 截断处理**（#10）：CONTINUE（默认，保留截断前缀于历史 + system 续写指令）/ CONDENSE（丢弃不完整前缀 + 精简重答指令，原文保留 TurnRecord.response）；重试上限 max_truncations（AgentLoop 参数，默认 2）防小窗口无限续写烧 token → TRUNCATION_LIMIT；length 与 tool_calls 并存时工具路径优先（残缺 arguments 由畸形 JSON 可操作错误兜底自纠错）。
- **TurnRecord / LoopResult**：每 Turn 结束消息历史浅拷贝快照 + 响应全文（供 P0-6 checkpoint 落盘）；结果含完整历史（可直接续接下一轮 run）、outcome、usage/token 累计、耗时。
- **测试基建**（tests/mock_llm.py）：ScriptedModel 实现 ChatModel 协议（Seam 1 零改动）——脚本化序列/固定返回两模式 + 每轮请求快照记录（轨迹断言 #62 数据源）+ 脚本耗尽显式报错；issue 09 将在此扩展录制回放为正式三模式 MockLLM。
- 验收证据：新增 35 个 loop 用例（核心 11 / 并行 5 / 自纠错 4 / guard 8 / 截断 7，其中并发验证用门控同步「串行实现下第二个工具永不进入」严格证明非串行）；全量 186 passed + 6 skipped（integration 门控）；Ruff check + format 零告警。
- **code-review 修复**（双轴审查后）：`elapsed_ms` docstring 与实现不符（未 start 恒 0 修正为 None 字段）；max_truncations 从 LoopGuard 错位移归 AgentLoop（guard 只留三种软限制，判定与配置同处）；guard 非法参数改 GuardConfigError（对齐 ConfigError 错误族）；wall-clock 测试改用固定时钟注入（消除 10ms 真实时间余量的 flaky 风险）；`LoopResult.content` 截断续写改为框架内拼合（原仅文档说明「需由调用方拼合」）——CONTINUE 前缀跨轮累积、终止时与尾段拼合，工具轮打断拼合链（工具轮前的正文属过程叙述，不混入最终答案）；拼合只影响 content，messages / turns 保真不变；弱断言 finish_reason 精确化。

**2026-09-11 提交后补强**（commit `c1f079d` reasoning 契约与参数透传、`61aff38` 测试覆盖扩展）：在 09-10 交付基础上对照 DeepSeek 官方文档（思考模式 / 对话前缀续写）逐条核对，并用真实端点实测验证。

### 1. reasoning 回填契约（#11）—— 结论保留，记录实测

官方《思考模式》文档要求：请求携带 `tools` 时，后续**所有**请求必须完整回传 `reasoning_content`，否则 400；且回传后会被拼接进上下文。

**本机实测（2026-09-11）11 组条件均未复现该 400**：

| 变量 | 取值 |
|------|------|
| 模型 | `deepseek-flash` / `deepseek-v4-pro` / `deepseek-reasoner` / `deepseek-chat` / `deepseek-v4-flash` |
| 端点 | 默认 / `beta` |
| 传输 | httpx 裸调 / **官方 SDK 样例逐行照搬** |
| 流式 | 是 / 否 |
| 回传形态 | 缺失 / 空串 / `null` / **部分回传**（turn1 有、turn2 无） |

每组均先确认 turn1 真实产出 reasoning 且返回 tool_calls。**不复现 ≠ 契约不存在**（社区有真实 400 报告，触发条件可能更窄或灰度中）。

**决定：按官方文档执行回填。** 理由不止「怕 400」，更是保留**交错思考** —— 模型跨工具调用复用推理链，这是文档给出的原理，探针测不出质量差异。

影响：`assistant_wire` 回填 `reasoning_content`；沿路清除了 6 个设计文档 + 3 处代码注释里「不入历史 / 不回填历史」的**错误表述** —— 原文案把「wire 历史」与「用户可见会话历史」混为一谈（后者由 Message 表的 `reasoning` 列承载，前端折叠展示）。

### 2. 思考模式参数透传（新能力）

`ChatModel.generate` 新增三个参数，优先级同 `temperature`（调用级 > 实例默认 > 不传）：`max_tokens` / `thinking: bool`（→ `{"thinking":{"type":"enabled"/"disabled"}}`）/ `reasoning_effort`。`AgentLoop` 逐轮透传，`ScriptedModel` 同步记录。

SDK 适配器把 `thinking` / `reasoning_effort` 经 **`extra_body`** 合并（不依赖 SDK 版本是否认识这两个 DeepSeek 特有形参，结果与 httpx 裸写 body 一致），新增 2 条契约测试锁定双适配器请求体逐字节同构。

**`max_tokens` 的额外价值**：补上了「无法主动制造 length 截断」的验证缺口 —— 现在能稳定复现截断，从而实测了 CONTINUE 拼合（见 §4）。

### 3. 思考模式的采样约束（实测）

| 参数 | 思考模式下的行为 |
|------|----------------|
| `temperature` | **不生效** —— 设置不报错但被上游忽略（静默失效，最易误导） |
| `top_p` | 下限 **0.95**（更小值被静默抬升）；非思考模式恒为 1.0 |
| `seed` | 仅 **content 可复现**，reasoning 每次不同（实测同输入两次：676 / 780 字符） |

另：思考模式**默认开启且 effort=high** —— 项目此前一直以最贵档在跑，且无任何控制入口。三条约束已写入 `ChatModel` / 两个适配器 / `AgentLoop` 的 docstring 与相关设计文档。

### 4. 对话前缀续写：评估后**不采用**

官方提供 `prefix: True` + `base_url=/beta` 的对话前缀续写。不引入的理由：① `base_url` 是 client 级配置，为一条边缘路径把全框架押上 beta 测试通道不划算；② 前缀续写要求末条消息为 `assistant`，与「`tool` 消息必须紧跟带 `tool_calls` 的 `assistant`」结构冲突；③ 与思考模式的交互官方未定义（定价页称 FIM 补全仅非思考模式可用）；④ 该特性本意是**输出格式引导**（强制代码块 / JSON 开头），不是截断续写。

**实测依据**：`max_tokens=100` 强造两次截断，两处接缝均为跨消息完整句（「…蜿蜒如」+「巨龙…」），最终 401 字**零重复** —— prompt 式续写已够用。详见 `docs/difficulties/01-core-loop.md` #10。

### 5. 集成测试门控改造

`RUN_INTEGRATION=1` 环境变量门控 → pytest `integration` marker + `addopts = -m "not integration"`。默认运行显示 `9 deselected`（语义正确，非「跳过」），运行方式改为 `pytest -m integration`（不再需要环境变量）。

**动因**：这套集成测试**此前从未跑过** —— 首次执行即暴露 3 个既有失败（模型名断言与端点归一后的回显不符）。默认「跳过」的输出给了虚假的覆盖感。

### 6. 真实端点防线（本次新增）

| 用例 | 覆盖 |
|------|------|
| `test_loop_multi_turn_tool_path_{httpx,sdk}` | 完整多轮工具路径 —— **scripted 测试的固有盲区**（fake 不校验 API wire 契约），也是 issue 04 当初唯一无真实防线之处 |
| `test_max_tokens_forces_length_truncation` | `max_tokens` 透传生效 + 截断链路端到端 |

**教训**：`ScriptedModel` 不校验 wire 契约，unit 层永远测不出「真实端点拒绝某种消息形态」这类问题 —— 「全量 N passed」在集成测试缺席时是虚假安全感。改动 model / agent 层后必须手动跑一次 `pytest -m integration`。

### 7. 测试覆盖扩展（190 → 203）

新增 13 条（commit `61aff38`），补齐以下真实场景：边叙述边调工具（`tool_call_response` 原硬编码 `content=None`，该形态**根本无法表达**）、`content_filter` 在 loop 层的终止语义、LoopGuard 纯单元判定（边界含等号 / 三限制优先级 / None 维度不参与 / 注入时钟 / 未 start 的 `elapsed_ms`）、复用实例的 run 级状态隔离、截断轮 token 计入预算、同一消息内已知 + 幻觉工具混合。guard 边界用例经变异测试验证非空转。

### 当前验收

- 单元：**203 passed / 9 deselected**
- 真实端点：**9 passed**（`pytest -m integration`）
- Ruff check + format 零告警
