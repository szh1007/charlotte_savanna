# 31 — 管理员后台（数据 & 用户管理）

**What to build:** 管理后台数据概览、用户管理（封禁/解封/改角色）。

**Blocked by:** 04 — User 模型, 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] `GET /api/admin/dashboard`：返回今日新增用户数、总帖子数、今日新增帖子数、总评论数、总打赏流水额
- [ ] `GET /api/admin/users`：用户列表（分页+搜索 phone/nickname），显示 role/状态/统计
- [ ] `PATCH /api/admin/users/{id}/role`：修改用户角色（user/moderator/admin）
- [ ] `PATCH /api/admin/users/{id}/ban`：封禁用户（is_deleted=True，清除其 token 到 Redis 黑名单）
- [ ] `PATCH /api/admin/users/{id}/unban`：解封用户（is_deleted=False）
- [ ] 操作写入 OperationLog（关联 ticket 33）
- [ ] 仅 role=admin 可访问
