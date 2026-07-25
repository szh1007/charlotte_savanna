# 17 — Collection 收藏

**What to build:** Toggle 收藏/取消收藏，收藏列表查询。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] 创建 `Collection` 模型：id, user_id, post_id, created_at — 联合唯一索引 (user_id, post_id)
- [ ] `POST /api/posts/{id}/collect`：toggle 逻辑 — 已收藏则删除记录并 post.collection_count-1，未收藏则创建记录并 post.collection_count+1
- [ ] 返回当前收藏状态 `{is_collected: true/false, collection_count: N}`
- [ ] `GET /api/user/collections`：当前用户收藏列表（分页，按收藏时间倒序）
