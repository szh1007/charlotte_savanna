# 28 — Celery — 定时排行刷新

**What to build:** Celery Beat 定时调度，每 5 分钟刷新热门帖子排行和缓存。

**Blocked by:** 25 — ZSET 热门排行 & 限流, 26 — Celery 骨架

**Status:** ready-for-agent

- [ ] 创建 `miniblog/tasks/cache_refresh.py`：
  - `refresh_hot_posts_zset()`：查询全部公开帖子 → 逐条计算 heat_score → `ZADD` 到 Redis ZSET → 保留 Top 200 → 更新热门帖子缓存
  - `refresh_category_hot_posts()`：按分区维度计算各自的热门排行
- [ ] Celery Beat 定时配置：`refresh_hot_posts_zset` 每 5 分钟执行一次
- [ ] `celery -A miniblog.tasks.celery_app beat --loglevel=info` 可启动
