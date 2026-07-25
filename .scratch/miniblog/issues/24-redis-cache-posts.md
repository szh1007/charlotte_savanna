# 24 — Redis 缓存（帖子层）

**What to build:** `@cache` 装饰器、帖子详情/广场列表/用户主页缓存。

**Blocked by:** 10 — Post + PostTag 模型

**Status:** ready-for-agent

- [ ] 创建 `miniblog/core/cache.py`：Redis 客户端连接 + `@cache(ttl, prefix)` 装饰器（基于函数名+参数 hash 生成 key，自动序列化/反序列化 JSON）
- [ ] `GET /api/posts/{id}` 帖子详情：缓存 TTL=10min，访问时刷新 TTL
- [ ] `GET /api/posts/feed` 广场列表：缓存 TTL=2min，按 `(page, page_size, category_slug)` 区分 key
- [ ] `GET /api/users/{id}/posts` 用户帖子列表：缓存 TTL=5min
- [ ] 帖子编辑/删除/可见性变更时主动使相关缓存失效
