# 35-P1-15 — 运行时上下文注入 + 挂起信号通道（ADR-0010）

**What to build:** 两条 P0 没有、P1 刚需的**通用通道**，外加控制面所需的仓储查询与状态机补态。**全部与业务无关** —— 框架只提供通道，阈值 / 规则 / 角色由应用注入。

**一、上下文注入 seam**：`tool/` 新增 `Injected()` 标记与 `ToolContext` 容器；**schema 生成器跳过带 `Injected` 的参数**（模型看到的 schema 里没有它，ADR-0009 的防注入性质不变）；`execute_tool(tool, *, arguments, context)` 在执行时填充；`AgentLoop.run(messages, run_id, context=...)` 携带 run 级上下文 —— **loop 仍可跨 run 复用**（保住 `CharAgent/agent/loop.py` 类 docstring 写下的设计意图）。形态对标 LangChain `InjectedToolArg` / Pydantic AI `RunContext` / FastAPI `Depends`。

**二、挂起信号通道**：工具**返回** `Suspension(kind, reason, payload)` 对象；executor 识别它并放进 `ToolExecution.suspension`（**不走 `_stringify` 文本化**）；loop 检测到非空即「落帧 → 置等待态 → 发对应事件 → 停下」。两种等待态用同一个 `Suspension` 的不同 `kind` 表达（内部审批 / 用户确认）。**不破坏 `execute_tool` 的「永不抛异常」契约**。

**三、状态机补态**：`RunStatus` 现有 8 态只有 `waiting_user`，新增**「等待内部审批」态**（不复用 `waiting_user` —— 放行者与超时策略都不同，合并会与 ADR-0009 的「两条路径代码不得耦合」冲突）。

**四、仓储查询**：`RunsRepository.list_by_status(status)` —— 框架侧真缺的那块（现在只有 `add/get/get_by_request_id/try_transition/set_status`）。

**不在本 issue**：HTTP 端点（`GET /runs?status=` 与 `POST /runs/{id}/resume`）属 P1-1，它依赖本 issue。

**为什么独立成 issue**：它动 P0 协议（`tool/decorator.py` 的 schema 生成、`tool/executor.py`、`agent/loop.py`），且 P1-1 与 P1-7 都要接它 —— 混进任一个都会让另一个的依赖关系说不清。决策与备选方案见 `CharAgent/docs/adr/0010-runtime-context-and-suspension-channels.md`。

**Blocked by:** 10

**Status:** ready-for-agent

- [ ] `Injected()` 标记 + `ToolContext` 容器落地（`ToolContext` 是通用容器，装什么由应用决定）
- [ ] schema 生成器跳过 `Injected` 参数 —— 用例：带 `ctx: Annotated[ToolContext, Injected()]` 的工具，产出的 schema 里**不含**该参数
- [ ] `execute_tool(..., context=)` 填充注入参数；未提供 context 而工具声明了注入参数时，给**可操作错误**而非 500
- [ ] `AgentLoop.run(..., context=)`；**用例：同一 loop 实例用不同 context 跑两次，工具拿到各自的值**（证明可跨 run 复用）
- [ ] 工具返回 `Suspension` → `ToolExecution.suspension` 非空，且**未被文本化回填给模型**
- [ ] **形状对齐（落地前先定）**：`checkpoint/utils/types.py` 现有的 `Suspension(reason, pending, approval_id)` 是**快照侧的挂起点**（记「还欠哪几条调用」）；本 issue 的工具返回值按 ADR-0010 §二要多带 `kind` / `payload`。两者是**同一类型扩字段**还是**拆成两个类型**（工具返回的挂起信号 vs 快照里的挂起点）尚未定 —— 决策前先看引用面：`checkpoint/serialization.py`（读写）、`checkpoint/utils/pending.py`（欠账扫描）、`checkpoint/utils/history.py`（历史表格）
- [ ] loop 检测挂起：落帧（`CheckpointSource.SUSPENSION`）→ 置等待态 → 发事件 → 停下；**用例：断言工具只执行一次、`turn_count` 接续、新帧 `parent_id` 指向挂起帧**
- [ ] `RunStatus` 新增「等待内部审批」态 + `ALLOWED_TRANSITIONS` 迁移规则 + 终态判定
- [ ] `RunsRepository.list_by_status(status)` + 用例
- [ ] 挂起记录携带 **initiator**（供应用做角色校验）
- [ ] 回归：P0 的 741 例全绿（`tool/` 与 `agent/` 是 P0 核心，改动必须零回归）
