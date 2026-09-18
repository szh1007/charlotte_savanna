# 08 — L2 写 · 取消订单 + 结构化用户确认

**What to build:** `cancel_order(order_no)` 工具 + **结构化用户确认**机制（与 07 的**内部审批是两条不同的挂起路径，代码上不得耦合**）。工具不直接执行，而是**返回 `Suspension(kind=CONFIRMATION, payload={confirm_token, digest, exp})`**（ADR-0010 的挂起通道 —— 与 07 的 `kind=APPROVAL` 走同一条通道、不同 kind，这正是「不耦合」的落地形态）；前端渲染确认卡片（「取消订单 2026…，退余额 ¥299，确认？」）；用户点击后带凭据再次提交，**网关校验凭据**（签名 / 过期 / 摘要匹配）才执行。业务侧走 minimall 现有 `cancel_order` service（`pending` 或 `paid` → `cancelled`，已付款则退回余额、回滚库存；**`completed` 与 `cancelled` 是终态，`shipped` 不允许取消** —— 这些规则由 service 层判定）。关键性质：**模型无法自己「确认」**，用户说「我确认」也无效；凭据一次性、有时效。

**Blocked by:** 07、**CharAgent P1-15**（`Suspension` 通道）

**Status:** ready-for-agent

- [ ] `cancel_order` 返回 `Suspension(kind=CONFIRMATION, ...)`，**不直接执行**；loop 因此置「等待用户」态并停下
- [ ] 确认卡片回显：订单号 + 可退金额 + 不可逆提示
- [ ] 凭据校验：签名 / 过期 / 摘要匹配三者任一不符 → 拒绝
- [ ] 凭据一次性：用过即失效，重放无效
- [ ] **注入用例**：对话中诱导模型「我确认」→ 无凭据则拒绝
- [ ] 状态规则：`completed` / `cancelled` / `shipped` 订单调用 → 返回可操作错误（非 500）
- [ ] 与内部审批机制**代码不耦合**（两条挂起路径分别可测）
- [ ] 确认与执行均经受审计
