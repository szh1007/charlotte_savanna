# 06 — minimall 退款能力（表 + service + 后台 worker）

**What to build:** minimall 侧从零补齐退款能力（现只有 `Order.Status.REFUNDED` 枚举值，**全仓库无代码路径能置为该状态**）。**退款单**：`Refund` 表 + 状态机 `pending → processing → completed | failed`（另加 `rejected`），字段含退款单号 / 订单 / 金额 / 原因 / 幂等键 / 时间戳；**余额流水**：`BalanceLedger` 表（现状是直接在 `profile.balance` 上加减，**无账本、无幂等**，`pay_order` 无 `select_for_update`、`RechargeView` 无 `atomic()` —— 一并补上并发保护）；**service 层**：创建退款单（按委托身份校验订单归属与售后资格）、推进状态、失败补偿；**后台 worker**：独立进程推进 `pending → processing → completed`，**同一事务内**改余额 + 写流水 + 置状态，`failed` 走 Saga 补偿；**幂等**：同 `request_id` 重放返回已有退款单，不产生第二次副作用。内部端点暴露给网关。

**代码落在哪**（2026-09-18 定，勿放错）：**全部在 `app/minimall/`** —— 退款单 / 余额流水表、service、后台 worker 都是 minimall 对**自身数据**的变更。`CharService/` 侧**没有** `worker/` 目录：worker 要「同一事务内改余额 + 写流水 + 置状态」，必须与 MySQL 同进程；若放 CharService，它就得持有一个业务库连接，直接违反 ADR-0008「agent 不直连业务库」，也会让 PRD §5.2 的「限流绕过 → 地址与凭证不存在，物理不可达」用例失效。

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] `Refund` 表 + 状态机 + 迁移落地（非法流转抛明确异常）
- [ ] `BalanceLedger` 余额流水表，退款 / 支付 / 充值全部记流水
- [ ] `pay_order` / `RechargeView` 补 `select_for_update` 与 `transaction.atomic()`（修正既有并发缺陷）
- [ ] 后台 worker 独立进程推进状态，同事务改余额 + 写流水 + 置状态（**代码在 `app/minimall/`**）
- [ ] 幂等：同 `request_id` 重放返回已有退款单，余额只变一次
- [ ] 失败路径：worker 中途失败 → `failed` + Saga 补偿（补偿动作本身也幂等）
- [ ] 内部端点（创建 / 查询 / 推进）可用，按委托身份校验归属
- [ ] 客服侧仍不直连库（**代码级检查**：`CharService/` 里不存在指向 minimall 库的连接配置）
