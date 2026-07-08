# 06 — 商品 API + 缓存

> Status: `ready-for-agent` | Type: `task` | Blocked by: 05

## 目标

实现商品列表（分页 + 搜索 + 筛选 + 排序）、商品详情、分类树的 DRF API，集成 Redis 缓存。

## 具体任务

1. `minimall/serializers.py`：
   - `CategoryTreeSerializer` — 递归子分类
   - `ProductListSerializer` — 列表用：id / name / slug / price / primary_image / category_name
   - `ProductDetailSerializer` — 详情用：全部字段 + images 嵌套 + 分类全路径
2. `minimall/filters.py`：
   - `ProductFilter(FilterSet)` — search (name + description), category (slug → 含子分类), min_price / max_price, ordering
3. `minimall/cache.py`：
   - `get_category_tree()` — 从缓存取，miss 时查库 + 写入，key=`category_tree`
   - `get_product_list(params_hash)` — 从缓存取分页结果，TTL 5min
   - `get_product_detail(slug)` — 从缓存取，TTL 10min
   - `invalidate_product_cache(product)` — 商品变更时删除对应缓存
   - `invalidate_category_cache()` — 分类变更时删除
4. `minimall/views_buyer.py`：
   - `ProductListView(ListAPIView)` — queryset 按 is_active 过滤，filterset_class, pagination
   - `ProductDetailView(RetrieveAPIView)` — lookup_field='slug'
   - `CategoryTreeView(APIView)` — 返回分类树 JSON
5. `minimall/urls_api.py`：挂载路由
6. `minimall/signals.py`：商品/分类变更时自动失效缓存

## 验收标准

- `GET /api/products/?search=手机&category=electronics&min_price=100&max_price=500&ordering=-price` 返回正确结果
- 分类树返回完整树形结构
- 商品详情含多图
- 第二次访问同一接口时命中缓存（日志/响应时间验证）
- Admin 中修改商品后，API 返回更新后的数据

## Comments

