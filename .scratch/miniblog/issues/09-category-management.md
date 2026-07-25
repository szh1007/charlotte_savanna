# 09 — Category 分区管理

**What to build:** 动态分区表、管理员 CRUD、预设种子数据、公开分区列表。

**Blocked by:** 05 — JWT 认证

**Status:** ready-for-agent

- [ ] 创建 `Category` 模型：id, name, slug(unique), description, icon, sort_order, is_active, moderator_id(FK→User, nullable), created_at, updated_at
- [ ] 管理员 `POST/PATCH/DELETE /api/admin/categories`：CRUD 分区，校验 slug 唯一
- [ ] 种子数据脚本：插入 9 个预设分区（科技/影视/动画/音乐/游戏/生活/番剧/知识/体育），slug 对应拼音
- [ ] `GET /api/categories`：公开，返回所有 `is_active=True` 的分区，按 sort_order 排序
- [ ] `GET /api/categories/{slug}`：返回单个分区信息
