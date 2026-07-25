# 07 — 个人资料 & 头像

**What to build:** 个人资料编辑、头像上传、用户空间查看。

**Blocked by:** 05 — JWT 认证

**Status:** ready-for-agent

- [ ] `GET /api/user/profile`：返回当前用户的完整个人信息（phone/nickname/email/avatar_url/bio/credits/role/统计数字）
- [ ] `PATCH /api/user/profile`：修改 nickname/email/bio（phone 不可改），校验 email 唯一
- [ ] `POST /api/user/avatar`：单文件上传到 `/miniblog/uploads/avatars/`，校验格式和大小，返回 `avatar_url`
- [ ] `GET /api/user/{id}`：查看他人公开信息（nickname/avatar/bio/统计数字/帖子列表），看不到 phone/email/credits
