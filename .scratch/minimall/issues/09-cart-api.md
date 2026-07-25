# 09 — 购物车 API

> Status: `ready-for-agent` | Type: `task` | Blocked by: 08

## 目标

实现购物车 CRUD 的 DRF API，仅买家可操作自己的购物车。

## 具体任务

1. `minimall/serializers.py`：
   - `CartItemSerializer` — product(id/name/primary_image/price), quantity, subtotal(computed)
   - `CartSerializer` — items(嵌套), total_amount(computed), total_count(computed)
   - `AddToCartSerializer` — product_id, quantity (默认 1)
   - `UpdateCartItemSerializer` — quantity
2. `minimall/views_buyer.py`：
   - `GET /api/cart/` — `APIView`, 返回当前用户购物车（无购物车时返回空结构）
   - `POST /api/cart/items/` — `APIView`, 添加商品（`get_or_create` Cart → 重复商品累加数量）
   - `PATCH /api/cart/items/{id}/` — `APIView`, 修改数量（0 = 删除）
   - `DELETE /api/cart/items/{id}/` — `APIView`, 移除单个商品
   - `DELETE /api/cart/clear/` — `APIView`, 清空购物车
3. `minimall/urls_api.py`：挂载路由
4. 所有视图 `permission_classes = [IsAuthenticated]`，操作前校验 cart 属于当前用户

## 验收标准

- 加购 → 购物车中有该商品
- 同一商品重复加购 → 数量增加，行数不变
- 数量修改为 0 → 等同于删除
- 数量超过库存 → 自动截断为库存值
- 清空后 → 返回空 items
- 未登录 → 401

## Comments
