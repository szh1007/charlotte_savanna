# 23 — Announcement 公告

**What to build:** 公告模型、管理员 CRUD、公开公告查询。

**Blocked by:** 05 — JWT 认证

**Status:** ready-for-agent

- [ ] 创建 `Announcement` 模型：id, title, content(TEXT), publisher_id(FK→User), is_pinned, effective_from(datetime), effective_to(datetime), is_deleted, created_at
- [ ] 管理员 `POST/PATCH/DELETE /api/admin/announcements`：CRUD 公告
- [ ] `GET /api/announcements`：公开，返回当前有效期内（`effective_from <= now <= effective_to`）的公告，置顶的排前面
