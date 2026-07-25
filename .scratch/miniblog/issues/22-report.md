# 22 — Report 举报

**What to build:** 用户举报帖子/评论、管理员审核处理。

**Blocked by:** 10 — Post + PostTag 模型, 15 — Comment 评论系统

**Status:** ready-for-agent

- [ ] 创建 `Report` 模型：id, reporter_id(FK→User), target_type(enum:post/comment), target_id(int), reason(TEXT), status(enum:pending/resolved/dismissed, default=pending), handler_id(FK→User, nullable), created_at, resolved_at
- [ ] `POST /api/reports`：用户举报帖子或评论（接收 target_type + target_id + reason），校验目标存在且未被删除，同一用户不能重复举报同一目标（pending 状态时）
- [ ] 管理员 `GET /api/admin/reports`：举报列表（分页，支持 ?status= 筛选）
- [ ] 管理员 `PATCH /api/admin/reports/{id}/resolve`：处理举报 — status=resolved 时执行对应操作（软删除目标帖子或评论），status=dismissed 时仅驳回不操作目标
- [ ] 处理完成后写入 OperationLog（关联 ticket 33）
