# 25 — ZSET 热门排行 & 限流

**What to build:** 热度公式计算、ZSET 热门帖子排行、登录接口限流。

**Blocked by:** 10 — Post + PostTag 模型, 24 — Redis 缓存（帖子层）

**Status:** ready-for-agent

- [ ] `GET /api/posts/hot`：从 Redis ZSET 读取热门帖子排行（按 heat_score 降序），支持 `?limit=` 参数，默认 20 条
- [ ] 热度计算函数 `calc_heat_score(view_count, like_count, comment_count, collection_count)`：权重从环境变量读取
- [ ] ZSET 刷新逻辑（由 Celery Beat 定时调用，本 ticket 做手动触发验证 `POST /api/admin/cache/refresh-hot`）
- [ ] 登录接口限流：`POST /api/auth/login` 60 秒内同 IP 最多 5 次，超限返回 `{code:0, message:"操作过于频繁，请稍后再试"}`
- [ ] 限流使用 Redis 计数器 + TTL 实现，不依赖第三方库
