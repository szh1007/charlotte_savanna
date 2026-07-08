# 11 — 收货地址 API

> Status: `ready-for-agent` | Type: `task` | Blocked by: 02

## 目标

实现买家收货地址的 CRUD API，支持默认地址管理。

## 具体任务

1. `minimall/models.py`：
   - `ShippingAddress` — user (FK→User, related_name='addresses'), receiver_name, phone, province, city, district, detail (TextField), is_default (BooleanField), created_at
2. `minimall/serializers.py`：
   - `ShippingAddressSerializer` — 全部字段
3. `minimall/views_buyer.py`：
   - `GET /api/addresses/` — `ListAPIView`, 当前用户的地址列表
   - `POST /api/addresses/` — `CreateAPIView`, 新建；is_default 为 True 时取消其他默认
   - `PATCH /api/addresses/{id}/` — `UpdateAPIView`, 修改
   - `DELETE /api/addresses/{id}/` — `DestroyAPIView`, 删除（默认地址不可删，先改默认）
4. `minimall/urls_api.py`：挂载路由

## 验收标准

- 可创建多个地址
- 设置默认后，旧默认自动取消
- 删除非默认地址成功
- 删除默认地址返回错误提示

## Comments

