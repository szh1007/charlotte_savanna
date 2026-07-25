# 27 — Celery — 通知 & 邮件 & Embedding 同步

**What to build:** 通知推送（可选异步增强）、密码重置邮件发送、帖子 embedding 同步到 Milvus。

**Blocked by:** 21 — Notification 通知, 06 — 邮箱密码重置

**Status:** ready-for-agent

- [ ] 创建 `miniblog/tasks/notification.py`：`send_notification_async()` — 如果 ticket 21 已改成异步通知，此任务处理通知写入
- [ ] 创建 `miniblog/tasks/email.py`：`send_reset_email(to_email, reset_url)` — 邮件发送任务（由 ticket 06 的 API 层调用）
- [ ] 创建 `miniblog/tasks/embedding.py`：
  - `sync_post_embedding(post_id)`：帖子发布/编辑后，生成 embedding 并 upsert 到 Milvus
  - `remove_post_embedding(post_id)`：帖子软删除后，从 Milvus 删除对应向量
  - `sync_post_visibility(post_id)`：帖子可见性变更后，更新/移除 Milvus 中的向量
- [ ] embedding 生成前需确认 Milvus 已初始化（ticket 29）
