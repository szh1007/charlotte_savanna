# 20 — BrowseHistory 浏览历史

**What to build:** 浏览记录写入、去重、最近 N 条查询。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] 创建 `BrowseHistory` 模型：id, user_id, post_id, visited_at — 联合唯一索引 (user_id, post_id)
- [ ] 查看帖子详情时自动写入 BrowseHistory：`UPSERT` 逻辑（已存在则更新 visited_at，不存在则插入）
- [ ] `GET /api/user/history`：当前用户的浏览历史（最近 N 条，默认 N=20，支持 `?limit=` 参数），按 visited_at 倒序，返回帖子摘要
- [ ] 浏览历史仅本人可见
