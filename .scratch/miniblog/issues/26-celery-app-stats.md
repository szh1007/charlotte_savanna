# 26 — Celery — 应用骨架 + 统计更新

**What to build:** Celery 应用实例、统计冗余字段异步更新任务。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] 创建 `miniblog/tasks/celery_app.py`：Celery 实例（broker=Redis db2, backend=Redis db3），配置任务序列化、时区、acks_late
- [ ] 创建 `miniblog/tasks/stats.py`：
  - `update_post_stats(post_id)`：重新 COUNT 并更新 post 的 view_count/like_count/comment_count/collection_count/tip_count/tip_total
  - `update_user_stats(user_id)`：重新 COUNT 并更新 user 的 post_count/follower_count/following_count
- [ ] ticket 11-20 中涉及统计更新的接口改为调用 Celery 异步任务而非同步更新
- [ ] `celery -A miniblog.tasks.celery_app worker --loglevel=info -P solo` 可启动（Windows）
