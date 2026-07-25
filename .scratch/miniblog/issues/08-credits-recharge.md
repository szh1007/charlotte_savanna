# 08 — Credits 充值

**What to build:** Credits 充值 API + 充值记录查询。

**Blocked by:** 05 — JWT 认证

**Status:** ready-for-agent

- [ ] 创建 `RechargeRecord` 模型：id, user_id(FK), amount, created_at
- [ ] `POST /api/user/recharge`：接收 amount(>0) → 写入 RechargeRecord → 更新 User.credits（事务内完成）→ 返回新余额
- [ ] `GET /api/user/recharge-records`：当前用户的充值记录列表（分页，按时间倒序）
