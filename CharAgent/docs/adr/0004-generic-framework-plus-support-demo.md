# ADR-0004: 通用框架 + 电商售后客服 demo 双形态

- 状态: accepted
- 日期: 2026-08-18（自 README 选型 0004 拆出；demo 领域于本次访谈细化）
- 考虑过的方案: 纯框架——拒绝，缺业务落地，面试时「框架能干嘛」说服力弱；纯业务——拒绝，框架级难点（并行工具、checkpoint 通用性）被业务逻辑掩盖
- 后果: 需维护框架层 + demo 层两套代码；demo 设计要覆盖全部框架难点

核心是业务无关的 agent runtime 库，另用一个智能客服 demo 自证可用。demo 领域定为**电商售后客服**：复用 minimall 订单语义（订单查询、物流追踪、退货/退款审批、发票、FAQ），高危审批 / 幂等补偿 / 转人工 / 多租户在售后场景均有真实业务含义。订单表通过独立只读查询层直连 MySQL（不 import Django ORM），Ticket / Escalation 等 demo 自属数据落 Postgres（与 checkpoint 同库、同 alembic 迁移体系）。
