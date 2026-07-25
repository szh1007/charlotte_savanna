# 16 — 缓存策略

> Status: `ready-for-agent` | Type: `task` | Blocked by: 01

## 目标

建立 minimall 的 Redis 缓存体系：缓存 key 约定、缓存读写辅助函数、信号驱动的失效机制。

## 具体任务

1. `minimall/cache.py`：
   - `CACHE_KEYS` 常量：`category_tree`, `product_detail:{slug}`, `product_list:{hash}`, `featured_products`, `hot_products`
   - `cache_category_tree(tree_data)` — 写入，无过期（手动失效）
   - `get_cached_category_tree()` — 读缓存 → miss → 查库 → 写缓存 → 返回
   - `get_cached_product_detail(slug, loader)` — 读 → miss → loader() → 写(TTL=600s) → 返回
   - `get_cached_product_list(params_hash, loader)` — 读 → miss → loader() → 写(TTL=300s) → 返回
   - `invalidate_product_cache(product)` — 删除 `product_detail:{slug}` + 模糊删除 `product_list:*`（用 `cache.delete_pattern`）
   - `invalidate_category_cache()` — 删除 `category_tree`
   - `get_cached_featured_products(loader)` — TTL=300s

2. `minimall/signals.py`：
   - `post_save` Product → `invalidate_product_cache(instance)`
   - `post_delete` Product → 同上
   - `post_save` / `post_delete` Category → `invalidate_category_cache()`
   - `post_save` / `post_delete` ProductImage → `invalidate_product_cache(instance.product)`

3. `minimall/apps.py`：`MinimallConfig.ready()` 中 import signals

## 验收标准

- 商品列表第二次请求 → 响应时间明显下降（< 50ms vs 首次 < 200ms）
- Admin 中修改商品 → 缓存立即失效 → 下次请求返回最新数据
- 新增分类 → 分类树 API 返回最新树
- Redis 中可查到缓存的 key（`redis-cli KEYS *`）

## Comments
