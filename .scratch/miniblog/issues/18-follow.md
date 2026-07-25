# 18 — Follow 关注

**What to build:** 单向关注/取关、粉丝列表、关注列表。

**Blocked by:** 04 — User 模型 + 注册 API

**Status:** ready-for-agent

- [ ] 创建 `Follow` 模型：id, follower_id, followed_id, created_at — 联合唯一索引 (follower_id, followed_id)
- [ ] `POST /api/users/{id}/follow`：toggle 逻辑 — 已关注则删除记录并 followed.follower_count-1 + follower.following_count-1，未关注则创建记录并 followed.follower_count+1 + follower.following_count+1
- [ ] 返回当前关注状态 `{is_following: true/false, follower_count: N}`
- [ ] `GET /api/users/{id}/followers`：某用户的粉丝列表（分页）
- [ ] `GET /api/users/{id}/following`：某用户关注的人列表（分页）
- [ ] 不能关注自己
