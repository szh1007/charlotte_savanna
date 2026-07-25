# 16 — Like 点赞

**What to build:** Toggle 点赞/取消点赞。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] 创建 `Like` 模型：id, user_id, post_id — 联合唯一索引 (user_id, post_id)
- [ ] `POST /api/posts/{id}/like`：toggle 逻辑 — 已点赞则删除记录并 post.like_count-1，未点赞则创建记录并 post.like_count+1
- [ ] 返回当前点赞状态 `{is_liked: true/false, like_count: N}`
- [ ] `GET /api/posts/{id}/like/status`：当前用户是否已点赞
