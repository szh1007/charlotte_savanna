# 10 — Post + PostTag 模型

**What to build:** Post 表和 PostTag 表，含全部统计冗余字段。

**Blocked by:** 02 — MySQL 引擎 + Alembic, 09 — Category 分区管理

**Status:** ready-for-agent

- [ ] 创建 `Post` 模型：id, title, content(TEXT, Markdown), author_id(FK→User), category_id(FK→Category), is_public(bool, default=True), view_count, like_count, comment_count, tip_count, tip_total, collection_count, heat_score(float), is_deleted, created_at, updated_at
- [ ] 创建 `PostTag` 模型：id, post_id(FK→Post, ondelete=CASCADE), tag_name(索引)
- [ ] Post 与 PostTag 建立 relationship
- [ ] Alembic migration 生成 + 执行
