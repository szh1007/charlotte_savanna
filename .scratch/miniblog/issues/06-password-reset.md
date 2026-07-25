# 06 — 邮箱密码重置

**What to build:** 密码重置流程（邮箱 → 重置链接 → 新密码），SMTP 邮件发送。

**Blocked by:** 04 — User 模型 + 注册 API

**Status:** ready-for-agent

- [ ] 创建 `PasswordResetToken` 模型：id, user_id(FK), token(JWT), expires_at, is_used
- [ ] 创建 `miniblog/core/email.py`：`send_email()`（smtplib + email.mime，SMTP 配置从环境变量读取）
- [ ] `POST /api/auth/forgot-password`：接收邮箱 → 查 User → 生成 30min JWT → 写入 PasswordResetToken → Celery 异步发送重置邮件（含 `https://域名/reset-password?token=xxx` 链接）
- [ ] `POST /api/auth/reset-password`：接收 token + 新密码 → 校验 token 有效性 + 未使用 + 未过期 → 更新 User.hashed_password → 标记 token is_used → Redis 将 token 加入黑名单
