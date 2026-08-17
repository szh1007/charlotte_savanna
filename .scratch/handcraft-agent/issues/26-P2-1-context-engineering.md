# 26-P2-1 — 上下文工程插件

**What to build:** 上下文工程插件（P2）：窗口管理、动态组装、工具结果截断、lost-in-the-middle 缓解、prompt 缓存；意图识别 + 澄清（IntentClarification：表达不清/指代模糊时反问确认）。经 hook（before_turn）挂载，配置注册 + 惰性 import（ADR-0007），核心零改动。

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] 窗口管理 + 动态组装（before_turn hook 注入）（#8/#9）
- [ ] 工具结果截断：超长结果摘要化（#8）
- [ ] lost-in-the-middle 缓解（关键信息置首尾）（#9）
- [ ] 意图识别 + 澄清反问（#9）
- [ ] 插件经配置注册挂载，核心零 import（ADR-0007）
