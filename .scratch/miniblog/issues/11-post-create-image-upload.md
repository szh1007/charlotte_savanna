# 11 — 帖子发布 & 图片上传

**What to build:** 发布帖子 API、Markdown 图片上传接口。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] `POST /api/posts`：接收 title + content(Markdown) + category_id + tags(list[str]) + is_public → 创建 Post + 批量创建 PostTag → 返回帖子详情
- [ ] 发帖后 user.post_count +1（同步更新）
- [ ] `POST /api/posts/upload-image`：单/multiple 文件上传到 `/miniblog/uploads/posts/`，校验格式(jpg/png/gif/webp)，返回 `![alt](url)` 格式的 Markdown 图片语法
- [ ] 仅登录用户可发帖，tags 去重并统一转小写存储
