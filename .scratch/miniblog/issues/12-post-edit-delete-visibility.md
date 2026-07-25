# 12 — 帖子编辑 & 软删除 & 可见性

**What to build:** 帖子编辑、软删除、公开/私密切换。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] `PATCH /api/posts/{id}`：仅作者可编辑，title/content/category_id/tags/is_public 均可修改，tags 全量替换（删旧增新）
- [ ] `DELETE /api/posts/{id}`：软删除（`is_deleted=True`），仅作者或管理员可操作
- [ ] `PATCH /api/posts/{id}/visibility`：切换 is_public，仅作者可操作
- [ ] 编辑/删除/可见性变更后，相关的缓存失效
