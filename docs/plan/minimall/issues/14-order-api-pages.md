# 14 — 订单 API 与页面

> Status: `ready-for-agent` | Type: `task` | Blocked by: 13

## 目标

实现订单相关的 DRF API 和前端模板页面。

## 具体任务

### API (`minimall/views_buyer.py`)

1. `GET /api/orders/` — `ListAPIView`, 当前用户订单，按 created_at 倒序，分页
2. `POST /api/orders/` — `APIView`, 调用 `create_order(user, cart_item_ids, address_id)`
3. `GET /api/orders/{order_no}/` — `RetrieveAPIView`, 订单详情（含 items, 状态时间线）
4. `POST /api/orders/{order_no}/pay/` — `APIView`, 接收 `payment_password`, 调用 `pay_order()`
5. `POST /api/orders/{order_no}/cancel/` — `APIView`, 调用 `cancel_order()`
6. `POST /api/orders/{order_no}/receive/` — `APIView`, 调用 `receive_order()`

### Serializer

- `OrderListSerializer` — order_no / status / total_amount / item_count / created_at
- `OrderDetailSerializer` — 全部字段 + items 嵌套 + status_timeline (computed)
- `CreateOrderSerializer` — cart_item_ids (list), address_id

### 页面模板

7. `templates/minimall/checkout.html`：确认订单页 — 地址选择、商品明细、总价、下单按钮
8. `templates/minimall/order_list.html`：我的订单列表
9. `templates/minimall/order_detail.html`：订单详情 — 状态时间线、操作按钮（支付/取消/确认收货）
10. `minimall/views_pages.py` + `minimall/urls_pages.py`：挂载页面路由

## 验收标准

- 下单 → 返回订单号 + 订单详情
- 支付 → 状态变"已付款"，paid_at 有值
- 取消 → 状态变"已取消"
- 确认收货 → 状态变"已收货"
- 订单列表/详情页正常展示
- 非本人订单访问 403

## Comments

