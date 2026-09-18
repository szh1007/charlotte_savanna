# 18-P1-8 — 业务降级兜底

> **2026-09-18 修订**（ADR-0008 + 边界复核）：**降级出口归位**。本 issue 只交付**分类与映射**，不交付话术与出口。原写在这里的「模板回复 / FAQ 匹配 / 转人工建议」是**产品策略** —— [issue 05 §3](05-P0-4-stream-events-hooks.md) 早已定过「降级话术是产品文案，归 server，框架只给事实性说明」；那个 server 已随 ADR-0008 外移到 `CharService/`，策略也要跟着走。

**What to build:** 框架侧的**降级分类与映射层**：

- **错误码分类**：`LLM_DOWN` / `LLM_TIMEOUT` / `RAG_DOWN` / `TOOL_ERROR` / `RATE_LIMITED` / `DEPENDENCY_DOWN` / `CANCELLED`，各自的**语义边界**（例如什么算 LLM_DOWN：熔断打开 vs 单次超时）
- **`LoopOutcome → error code` 映射**：框架把「怎么结束的」翻译成「属于哪类错误」，供应用选降级路径
- **`TERMINAL_ERROR_TEXT`**：给调用方的事实性说明（不是话术）
- **降级策略 SPI**：应用可注册 `on_degrade(code)` 之类，由应用决定说什么、做什么（默认不降级，原样上报错误码）
- **`DEPENDENCY_DOWN`**：下游依赖不可用的**通用**错误码（2026-09-18 由 `GATEWAY_DOWN` 改名 —— 框架不该知道「网关」这个业务拓扑）

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] 错误码分类与语义边界写清（每类的判定条件）
- [ ] `LoopOutcome → error code` 映射表 + 用例覆盖每种结束原因
- [ ] `TERMINAL_ERROR_TEXT` 提供事实性说明
- [ ] 降级策略 SPI 可注册、可替换
- [ ] `DEPENDENCY_DOWN` 成为通用码（框架代码里不出现业务拓扑词汇）
- [ ] 测试：**每个错误码的映射**（不是「每条降级话术」——话术在应用侧）
- [ ] **出口移出**：模板回复 / FAQ 匹配 / 转人工建议 → `CharService/issues/09`（降级汇入转人工）与 `03`（FAQ 检索）

---

**2026-09-18 修订（已被上方修订取代，保留作演进记录）**（ADR-0008）：曾新增一个错误码分支 `GATEWAY_DOWN` —— 后按边界复核**改名为通用的 `DEPENDENCY_DOWN`**（框架不该知道「网关」这个业务拓扑），且「只读仍可用 / 写操作拒绝并建议转人工」这条**出口策略已移出**到 `CharService/issues/09`。**以下表述以文件上方的修订为准**：框架只交付错误码分类 + 映射 + 降级策略 SPI。
