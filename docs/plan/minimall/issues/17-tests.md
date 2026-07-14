# 17 — 测试

> Status: `ready-for-agent` | Type: `task` | Blocked by: 14, 15, 16

## 目标

为 minimall 编写测试覆盖：模型、API 端点（含权限边界）、服务层业务逻辑、订单状态流转。

## 具体任务

### `minimall/tests/test_models.py`

- User 创建、payment_password 哈希
- Category 树形结构（MPTT parent/children/descendants）
- Product basic + 多图片关系
- Cart → CartItem unique_together 约束
- Order 状态枚举 + order_no 唯一
- OrderItem 快照字段

### `minimall/tests/test_api_products.py`

- 商品列表分页
- search 筛选
- category 筛选（含子分类）
- 价格区间筛选
- 排序
- 下架商品不展示
- 分类树 API

### `minimall/tests/test_api_cart.py`

- 未登录访问 → 401
- 加购 → 200
- 重复加购 → 数量累加
- 修改数量
- 删除商品
- 清空购物车
- 超过库存 → 截断

### `minimall/tests/test_api_orders.py`

- 下单成功 → 库存扣减
- 下单库存不足 → 错误
- 支付正确密码 → 状态变 paid
- 支付错误密码 → 错误
- 取消订单 → 库存恢复
- 已发货不可取消
- 确认收货
- 非本人订单 → 403

### `minimall/tests/test_services.py`

- `create_order` — 事务内扣库存 + 清购物车
- `create_order` — 并发安全（可用两个线程模拟）
- `pay_order` — 密码校验
- `cancel_order` — 库存恢复
- 状态流转约束（每个状态的合法下一状态）

## 验收标准

- `python manage.py test minimall` 全部通过
- 覆盖关键权限边界（匿名/买家/管理员）
- 覆盖订单完整状态流转

## Comments
