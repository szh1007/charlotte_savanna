# 21 — Notification 通知

**What to build:** 通知模型、通知列表、未读计数、标记已读。

**Blocked by:** 05 — JWT 认证, 10 — Post + PostTag 模型, 15 — Comment 评论系统, 16 — Like 点赞, 18 — Follow 关注, 19 — Tip 打赏

**Status:** ready-for-agent

- [ ] 创建 `Notification` 模型：id, receiver_id(FK→User), sender_id(FK→User), type(enum:like/comment/reply/follow/tip), target_type(enum:post/comment), target_id(int), is_read(bool, default=False), is_deleted, created_at
- [ ] 在 like/comment/reply/follow/tip 操作发生时，同步创建 Notification 记录（不做异步，保证通知不丢）
- [ ] `GET /api/notifications`：当前用户的通知列表（分页，按时间倒序，排除 is_deleted）
- [ ] `GET /api/notifications/unread-count`：返回未读通知数量 `{count: N}`
- [ ] `PATCH /api/notifications/{id}/read`：标记单条为已读
- [ ] `PATCH /api/notifications/read-all`：一键全部已读（可选快捷操作）
