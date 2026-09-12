# 05-P0-4 — 流式事件状态机 + hook 注册表骨架

**What to build:** StreamEvent 事件总线：thinking / tool_call / tool_result / final 四类事件 + reasoning 独立事件（#11，前端折叠展示的独立通道，不混入正文；wire 历史照旧回填）+ error 异常终止事件；事件带 seq 序号支持断点续拉。hook 注册表骨架（before_turn / after_turn / on_model_call / on_tool_executed / on_event）P0 落地，空注册零成本——P2 模块（memory/cost/observability）经此挂载（ADR-0007），核心零 import P2。

**Blocked by:** 01

**Status:** done

- [x] 事件类型定义（落地为六类：thinking / tool_call / tool_result / reasoning / final / error）
- [x] 事件状态机：合法转换（thinking → tool_call → tool_result → final）不可乱序（四条不变量强校验）
- [x] 每事件带 seq 序号
- [x] hook 注册表骨架：5 个 hook 点 + 注册 API，空注册零开销（ADR-0007 扩展点）
- [x] reasoning 事件与历史分离验证（#11）：独立事件 + 不混入 content，**且 wire 历史仍回填**（措辞已按 issue 04 §1 修正后的契约执行，原「不入历史」表述作废）
- [x] delta 与 final 的权威性约定：delta 仅作渐进预览，`final.content` 为权威值（前端收到即覆盖缓冲）——CONDENSE 丢弃的截断前缀不进 final（#10）
- [x] thinking / final 的文本边界：**工具轮**的 assistant 正文归 thinking（判据即 loop 侧「工具轮打断拼合链」的同一判断），终止轮正文才归 final；**截断轮例外**（正文属答案素材，发了会重复展示或暴露已作废内容，见 Comments §4）
- [x] guard 刹车（MAX_TURNS / TIME_LIMIT / TRUNCATION_LIMIT，此时 LoopResult.content=None）的事件契约：**走 error 事件**，定案见 03-api.md §2.2

## Comments

**2026-09-12 实施完成**（提交前 code-review 双轴审查 + 修复）。落点：`CharAgent/stream/`（事件契约与状态机）+ `CharAgent/hooks/`（扩展点骨架）+ `CharAgent/agent/loop.py`（接线）；静态零件按既有惯例收进各包 `utils/`（`stream/utils/{types,errors}.py`、`hooks/utils/{types,errors}.py`、新增 `agent/utils/events.py`）。两侧门面 `__init__.py` 导出公共 API。

- **模块落点**：DESIGN.md 目录结构写的 `stream/` + `hooks/` 落地为顶层包（issue 04 把 loop 收进 `agent/` 包，本轮不再移动既有代码）。事件契约常量（`TOOL_RESULT_SUMMARY_LIMIT` / `TERMINAL_TYPES`）随 stream 包走，loop 侧载荷构造在 `agent/utils/events.py`。

### 1. 事件模型（六类）

| 事件 | 载荷（除 type/seq） | 说明 |
|------|-------------------|------|
| thinking | message, turn | 非终止轮正文（过程叙述） |
| tool_call | tool_call_id, tool_name, arguments, status="started", turn | `arguments` 为**原始 JSON 字符串**（#10 不预解析） |
| tool_result | tool_call_id, tool_name, status, summary/duration_ms 或 error, turn | 成功带截断摘要（≤200 字符），失败带可操作错误（#2） |
| reasoning | delta, turn | 旁路通道，不改变主序列状态 |
| final | content, finish_reason, outcome, tokens, elapsed_ms | 正常结束；content 为权威值 |
| error | error={code, message} | 异常结束；code 取 LoopOutcome 值 |

`run_id` 不入框架层事件（框架无 run 概念），由 P1 server 转发时注入；`seq` 即 `after_event_id` 的取值。

### 2. 状态机四条不变量（违反抛 `EventSequenceError`，产出瞬间拦下）

① seq 每 run 从 1 单调递增；② tool_result 必须匹配未闭合的 tool_call（按 id 配对，同 id 不得开两次）；③ 工具未回填完不得发终局事件；④ 终局后不得再发任何事件。这与 #10 的 wire 消息配对约束是同一条规则在两条通道上的体现 —— 乱序事件流等于把 bug 直接画到用户屏幕上。

`reasoning` 不参与主序列（任意非终局位置可发）；**终局事件恰好一个**，由 `_emit_terminal` 单一出口产出（在最后一轮 after_turn hook 之后）。

### 3. guard 刹车契约（定案 → 03-api.md §2.2）

**走 `error`，不发 `final`**：刹车时 `LoopResult.content=None`，发 final 等于「终局答复却没有答复」；降级话术（模板回复 / 转人工）是产品文案，归 P1-8 server，框架只给事实性说明（`TERMINAL_ERROR_TEXT`，code 取 LoopOutcome 值 + `content_filter`）。同时定案：框架层异常（模型调用失败）直接抛出、**不发终局事件**；取消的 `error(cancelled)` 由 P1-2 server 产出（框架在 CancelledError 传播路径上不做 await，无法安全发事件）。

### 4. 文本边界与 delta 权威性

- 工具轮正文 → `thinking`（判据与 `content_parts.clear()` 同源）；终止轮正文 → `final`；无叙述不发空 thinking
- `delta` 仅作渐进预览、`final.content` 为权威值（前端收到即覆盖缓冲）：CONTINUE 拼合后的 content 与 final 一致，CONDENSE 丢弃的前缀不进任何事件
- **P0 未做 token 级流式**：`ChatModel.generate` 返回完整响应，故每次响应产出一条完整 reasoning delta（ticket 第 1 项即限定「依赖 ModelResponse 的 reasoning 结构」）；token 级切分需给模型协议加 delta 回调，属 P1 流式 server 范围

### 5. hook 注册表（ADR-0007）

- 五个点与载荷见 `hooks/registry.py` docstring 与 01-architecture.md §4.2；载荷以关键字参数传递，`before_turn` 拿到的 `messages` 是**活引用**（memory 插件注入记忆的挂载点，P2-3）
- 空注册零开销（无回调调用、无 await 挂起点）；插件抛 `Exception` 被隔离并记入 `registry.failures`（不拖垮核心，但不静默）；**`CancelledError` 直接传播**（插件不得挡住 kill switch，#3）
- 双通道分工：`event_sink` 是事件出口（传输必须可靠，异常向上传播），`HookPoint.ON_EVENT` 是扩展点（异常隔离）—— 同一份事件喂两条通道

### 6. 验收证据

- 新增 48 个用例：`test_stream.py`（事件类型 / seq / 四条不变量 / 旁路通道 / 分发顺序）· `test_hooks.py`（注册 / 顺序 / sync+async / 异常隔离 / CancelledError 不被吞）· `test_loop_events.py`（完整事件序列快照 / 文本边界 / reasoning 双通道 / 四种 guard 刹车 + content_filter + 上游中断的终局规则 / 空正文仍 final / 工具失败与未知工具 / hooks 时机与载荷 / before_turn 注入 / 回归）
- 全量 **281 passed / 11 deselected**（integration marker 默认排除）；真实端点 **11 passed**（`pytest -m integration`，25.4s——含多轮工具路径与强制截断，本次 loop 主体被改过故必跑）；Ruff check + format 零告警
- 文档同步：03-api.md §2（事件协议 + 状态机 + 终局规则 + delta 权威性，含 §4 与本层码的分工）· 01-architecture.md §3/§4（终局出口 + 事件总线与 hook 表补载荷/落点/隔离语义）· difficulties/01-core-loop.md #4（实现要点）· CONTEXT.md（StreamEvent / Hook 词条）· 05-roadmap.md（P0-4 落点与事件数）· DESIGN.md / ADR-0007 / PRD §4.6/§4.8（订正「四类事件」与 reasoning「不回填历史」的过时表述，后者为 issue 04 §1 已定案的清理漏项）

### 7. code-review 双轴审查修复（2026-09-12）

- **不变量②措辞与实现对齐**：原写「同一 id 只能开一次」过强 —— 真实上游的 `tool_call_id` 逐响应重置（`call_0` 每轮重来），跨轮复用属正常；改为「同一 id 在**闭合前**不得重复开启」，并补测（同 id 闭合后可再开启）
- **终局判据精确化**：原 §2.2 表述为「拿到答复才发 final」，与「`stop` + 空正文仍发 final(content=null)」冲突；改为按「run 是怎么结束的」判定，并补测钉住该行为；`content_filter` 明确为按 `finish_reason` 归类（即便模型已吐出部分文本）
- **文本边界收窄并写明理由**：截断轮不发 `thinking`（ticket 原文「非终止轮（工具轮等）」的字面范围过宽，实施后据 §2.4 收窄为「工具轮」，理由见 Comments §4）
- **示例自洽**：03-api.md §2 的 `tool_result(call_2)` 缺配对的 `tool_call`，与同节不变量②矛盾 —— 补齐并重排序号
- **清理**：删除无调用的 `EventBus.closed`；`summarize` 去掉无调用方的 `limit` 形参；`AgentLoop._fire` 中间层删除（对齐 `guard=None → LoopGuard()` 惯例，改为默认空注册表，消掉与 `EventBus` 重复的判空）；终局事件改由 `emit_terminal(bus, result)` 从 `LoopResult` 派生（同一份 `elapsed_ms`，不再读两次）；`ModelCallPhase` 枚举替换 `"before"/"after"` 字面量；`mock_llm.tool_call_response` 补 `reasoning` 形参（去掉测试里的本地替代工厂）
- **未采纳**：`_FakeClock` 与 `test_loop_guard.py` 的重复保留（重复 2 次，未达项目 DRY 阈值 ≥3；`tests/helpers.py` 的定位是 wire 样本与常量，不宜混入测试替身）

### 8. 可读性重构：run() 按功能拆分（2026-09-12，交付后追加）

`run()` 原为 138 行 / 118 代码行的巨型函数，拆成编排层 + 6 个分支方法（每个带总注释 docstring）：

| 方法 | 代码行 | 职责 |
|------|-------|------|
| `run()` | 118 → **47** | 只做编排：guard 判定 → 决策 → 分支分派 → 轮次收尾 → 终局事件 |
| `_decide()` | 42 | 一次模型决策：before_turn / on_model_call(before+after) hook + generate + 记账 + reasoning 事件 |
| `_handle_tool_turn()` | 31 | 工具轮：叙述归 thinking → assistant 入历史 → 并行执行 → 回填 + 事件 + hook |
| `_handle_truncation()` | 21 | length 截断：超限放弃 / CONDENSE 精简 / CONTINUE 续写 |
| `_handle_server_interrupted()` | 3 | 上游中断：半截不当答复 |
| `_handle_completion()` | 3 | 自然终止：拼合续写各段 |
| `_record_turn()` | 19 | 每轮快照（供 checkpoint）+ after_turn hook |

- 可变状态（原先散在 `run()` 里的 9 个局部变量）收进 `LoopState`（`agent/utils/types.py`），否则每个分支方法要传一长串参数或返回元组
- **命名注意**：该类型初名 `RunState`，与词表（CONTEXT.md）已有的 `RunState`（= P1-2 的**运行状态机** created/running/.../cancelled，有合法迁移规则、持久化在 run 记录）撞名，故改名 `LoopState` 并写入词表消歧 —— 数据袋 ≠ 状态机
- 行内注释零丢失：19 条原样保留；18 条段级块注释随其解释的语句进入对应方法的 docstring（比对脚本验证）；模块 docstring 补了拆分结构图
- 验证：单测 281 passed（重构前后一致）· 真实端点 11 passed（期间 `test_loop_multi_turn_tool_path_httpx` 抖动失败 1 次 / 复跑 5 次通过，该用例依赖「模型必产出 reasoning」的模型侧前提）· Ruff 零告警

### 9. 遗留（不属本 issue）

token 级 content/reasoning delta（P1 流式 server，需改 `ChatModel` 协议）· `approval_required` 事件（P1-7 HITL）· `error(cancelled)`（P1-2 状态机 + server）· 事件持久化与断点续拉（P1-1 SSE 端点）· `final.citations`（P1-9 RAG 溯源）与 `final.cost`（P2 cost 插件）· 根 `CharAgent/__init__.py` 门面尚未导出 agent/stream/hooks 公共 API（既有不一致，与 P0-3 的 AgentLoop 同批处理）
