# 32 — 管理员后台（帖子 & 评论管理）

**What to build:** 管理员帖子列表/删除、评论列表/删除。

**Blocked by:** 10 — Post + PostTag 模型, 15 — Comment 评论系统

**Status:** ready-for-agent

- [ ] `GET /api/admin/posts`：全部帖子列表（分页，支持搜索 title + 按分区/可见性/is_deleted 筛选），含 deleted 帖子
- [ ] `DELETE /api/admin/posts/{id}`：强制软删除任意帖子
- [ ] `PATCH /api/admin/posts/{id}/visibility`：管理员强制修改帖子可见性
- [ ] `GET /api/admin/comments`：全部评论列表（分页，支持搜索 content + 按 is_deleted 筛选），含 deleted 评论
- [ ] `DELETE /api/admin/comments/{id}`：强制软删除任意评论
- [ ] 操作写入 OperationLog（关联 ticket 33）
