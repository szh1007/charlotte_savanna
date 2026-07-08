# 13 — 订单服务层

> Status: `ready-for-agent` | Type: `task` | Blocked by: 08, 11, 12

## 目标

实现订单核心业务逻辑：下单（含库存扣减+防超卖）、支付验证、取消、发货、确认收货。

## 具体任务

`minimall/services.py`：

1. `create_order(user, cart_item_ids, address_id)`：
   - 校验购物车非空
   - 校验地址属于当前用户
   - 开启事务：`with transaction.atomic()`
   - `select_for_update()` 锁住所有涉及商品行
   - 逐项校验库存，不足则抛异常
   - 扣减库存（`product.stock -= quantity` → `product.save()`）
   - 生成订单号 → 创建 Order
   - 逐项创建 OrderItem（快照商品名/价格）
   - 删除已下单的 CartItem
   - 返回 Order
2. `pay_order(order, payment_password)`：
   - 校验 order.status == 'pending'
   - 校验 order.user == current_user
   - `check_password(payment_password, user.payment_password)` → 不匹配抛异常
   - order.status = 'paid', order.paid_at = now, save
3. `cancel_order(order, user)`：
   - 校验 status in ('pending', 'paid') — 已发货不可取消
   - 开启事务：恢复库存
   - order.status = 'cancelled', order.cancelled_at = now
4. `ship_order(order)`：
   - 校验 status == 'paid'
   - order.status = 'shipped', order.shipped_at = now
5. `receive_order(order, user)`：
   - 校验 status == 'shipped'
   - order.status = 'received', order.received_at = now
6. 提取 `OrderService` 异常类：`InsufficientStockError`, `InvalidOrderStatusError`, `PaymentError`

## 验收标准

- 下单后库存正确扣减
- 并发下单同一商品 → 无超卖（select_for_update）
- 错误支付密码 → 支付失败
- 已发货订单 → 不可取消
- 取消订单 → 库存恢复

## Comments

