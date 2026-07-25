# 14 — 关键词搜索

**What to build:** 标题+正文关键词搜索 API。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] `GET /api/search`：接收 `?q=` 关键词，LIKE 匹配 title 和 content（MySQL `LIKE %keyword%`），分页，按时间倒序
- [ ] 搜索结果仅返回 is_public=True + is_deleted=False 的帖子
- [ ] 搜索为空时返回空列表 + pagination(total=0)
