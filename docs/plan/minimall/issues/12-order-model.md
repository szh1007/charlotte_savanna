# 12 — 订单模型

> Status: `ready-for-agent` | Type: `task` | Blocked by: 02, 05

## 目标

定义 Order + OrderItem 模型，Admin 中注册并实现自定义 action。

## 具体任务

1. `minimall/models.py`：
   - `Order` — order_no (CharField, unique, 流水号), user (FK→User), status (CharField, choices=OrderStatus, default='pending'), total_amount (DecimalField), shipping_address_snapshot (JSONField, 下单时快照), paid_at / shipped_at / received_at / cancelled_at (DateTimeField, nullable), created_at, updated_at
   - `OrderItem` — order (FK→Order, related_name='items'), product (FK→Product), product_name (快照), product_price (快照), quantity, subtotal
   - `OrderStatus` — TextChoices 枚举
2. `minimall/managers.py`：
   - `generate_order_no(user_id)` — `datetime.now().strftime('%Y%m%d%H%M%S') + str(user_id) + random 4 digits`
3. `minimall/admin.py`：
   - `OrderItemInline(TabularInline)` — 只读
   - `OrderAdmin(ModelAdmin)` — list_display: order_no / user / status / total_amount / created_at; list_filter: status; search: order_no; readonly: 大部分字段; actions: ship_orders
   - `ship_orders(self, request, queryset)` — 批量发货 action
4. `makemigrations` + `migrate`

## 验收标准

- Admin 中可查看全部订单
- 按状态筛选正常
- 订单详情页可看到 OrderItems
- 批量发货 action 可用

## Comments

