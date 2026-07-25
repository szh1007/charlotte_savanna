# 13 — 社区广场 & 帖子详情

**What to build:** 社区广场帖子流（分页+分区筛选）、帖子详情 API。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] `GET /api/posts/feed`：返回公开帖子列表（排除 is_deleted + is_public=False），按 created_at 倒序，支持 `?category_slug=` 分区筛选，分页
- [ ] `GET /api/posts/{id}`：返回帖子详情（含作者信息、分区信息、tags、全部统计冗余字段），访问时 view_count+1（写入浏览历史逻辑在 ticket 20）
- [ ] `GET /api/users/{id}/posts`：查看指定用户的公开帖子列表（分页）
- [ ] 列表接口返回数据含 `pagination` 元数据
