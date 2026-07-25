# 33 — 管理员后台（流水 & 操作日志）

**What to build:** 打赏流水+充值记录查看、操作日志自动记录+查询。

**Blocked by:** 08 — Credits 充值, 19 — Tip 打赏

**Status:** ready-for-agent

- [ ] 创建 `OperationLog` 模型：id, operator_id(FK→User), action_type(varchar, 如 delete_post/ban_user/change_role/resolve_report/publish_announcement 等), target_desc(TEXT), ip_address, created_at
- [ ] 创建 `log_operation(operator_id, action_type, target_desc, ip_address)` 工具函数，供其他管理员操作调用
- [ ] `GET /api/admin/transactions/tips`：打赏流水列表（分页，支持按 from_user/to_user 筛选，按时间倒序）
- [ ] `GET /api/admin/transactions/recharges`：充值记录列表（分页，支持按 user_id 筛选）
- [ ] `GET /api/admin/logs`：操作日志列表（分页，支持按 action_type/operator_id 筛选，按时间倒序）
- [ ] 在 ticket 31、32、22 的管理员操作中集成 `log_operation` 调用
