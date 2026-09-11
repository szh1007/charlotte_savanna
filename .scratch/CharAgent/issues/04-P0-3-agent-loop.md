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

- **主循环**（AgentLoop.run）：while 循环 = 模型决策 → 按 finish_reason 分支（tool_calls 优先 / length 截断 / 自然终止）→ 每轮结束记录完整消息历史快照。assistant(tool_calls) 消息先入历史再整体批量回填 tool 消息，保证配对结构（#10）；tool 回填按 tool_calls 原顺序（gather 保序），**非完成顺序**——历史稳定可重放、并行语义不退化（#1）；失败工具回填的是 execute_tool 的可操作错误文本，即自纠错载体（#2，错误进历史不进 traceback）；reasoning 回填 wire 历史（#11 + DeepSeek 思考模式契约：官方文档要求带 tools 的请求回传 reasoning_content，称缺失即 400，且会被 API 拼接进上下文；本机实测 2026-09-11 `deepseek-flash` 缺失未触发 400，框架仍按文档执行以保留交错思考），但不混入 content 字段，前端折叠展示归 reasoning 事件。
- **并行执行**：asyncio.gather + return_exceptions（单条失败不拖垮整体）；防御分支包「子任务被独立取消」等非预期异常为可操作失败结果；父 task cancel 时 gather 取消全部子任务并传播 CancelledError（kill switch 即时打断，不吞）。
- **LoopGuard**（三软限制）：max_turns / token 预算（usage 累计，无 usage 计 0）/ wall-clock（time_source 注入缝）；**软限制「这一轮结束后才判断」**——已发出的工具调用总执行完回填，历史恒合法，`check_after_turn` 在下一轮模型调用前判定；触发点经 outcome 上报（MAX_TURNS / TOKEN_BUDGET / TIME_LIMIT）。kill switch 不在 guard 内：外部 `asyncio.Task.cancel`，测试证明 <2s 打断 30s 慢工具且零任务泄漏。
- **length 截断处理**（#10）：CONTINUE（默认，保留截断前缀于历史 + system 续写指令）/ CONDENSE（丢弃不完整前缀 + 精简重答指令，原文保留 TurnRecord.response）；重试上限 max_truncations（AgentLoop 参数，默认 2）防小窗口无限续写烧 token → TRUNCATION_LIMIT；length 与 tool_calls 并存时工具路径优先（残缺 arguments 由畸形 JSON 可操作错误兜底自纠错）。
- **TurnRecord / LoopResult**：每 Turn 结束消息历史浅拷贝快照 + 响应全文（供 P0-6 checkpoint 落盘）；结果含完整历史（可直接续接下一轮 run）、outcome、usage/token 累计、耗时。
- **测试基建**（tests/mock_llm.py）：ScriptedModel 实现 ChatModel 协议（Seam 1 零改动）——脚本化序列/固定返回两模式 + 每轮请求快照记录（轨迹断言 #62 数据源）+ 脚本耗尽显式报错；issue 09 将在此扩展录制回放为正式三模式 MockLLM。
- 验收证据：新增 35 个 loop 用例（核心 11 / 并行 5 / 自纠错 4 / guard 8 / 截断 7，其中并发验证用门控同步「串行实现下第二个工具永不进入」严格证明非串行）；全量 186 passed + 6 skipped（integration 门控）；Ruff check + format 零告警。
- **code-review 修复**（双轴审查后）：`elapsed_ms` docstring 与实现不符（未 start 恒 0 修正为 None 字段）；max_truncations 从 LoopGuard 错位移归 AgentLoop（guard 只留三种软限制，判定与配置同处）；guard 非法参数改 GuardConfigError（对齐 ConfigError 错误族）；wall-clock 测试改用固定时钟注入（消除 10ms 真实时间余量的 flaky 风险）；`LoopResult.content` 截断续写改为框架内拼合（原仅文档说明「需由调用方拼合」）——CONTINUE 前缀跨轮累积、终止时与尾段拼合，工具轮打断拼合链（工具轮前的正文属过程叙述，不混入最终答案）；拼合只影响 content，messages / turns 保真不变；弱断言 finish_reason 精确化。
