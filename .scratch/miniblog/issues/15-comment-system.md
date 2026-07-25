# 15 — Comment 评论系统

**What to build:** 2 层扁平评论系统（一级评论 + 回复），软删除。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] 创建 `Comment` 模型：id, post_id(FK), user_id(FK), parent_id(FK→self, nullable), reply_to_user_id(FK→User, nullable), content(TEXT, Markdown), like_count, is_deleted, created_at
- [ ] `POST /api/posts/{id}/comments`：发表一级评论（parent_id=null）或回复（parent_id=一级评论ID, reply_to_user_id=被回复的用户ID）
- [ ] `GET /api/posts/{id}/comments`：返回所有评论，一级评论按时间倒序，每个一级评论下的回复按时间正序
- [ ] `DELETE /api/comments/{id}`：软删除，仅评论作者或管理员可操作
- [ ] 评论后 post.comment_count +1（同步更新）
- [ ] Alembic migration
