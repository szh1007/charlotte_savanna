# 08 — 购物车模型

> Status: `ready-for-agent` | Type: `task` | Blocked by: 02, 05

## 目标

定义 Cart + CartItem 模型，一个用户一个购物车。

## 具体任务

1. `minimall/models.py`：
   - `Cart` — user (OneToOneField→User, related_name='cart'), created_at, updated_at
   - `CartItem` — cart (FK→Cart, related_name='items'), product (FK→Product), quantity (PositiveIntegerField, default=1), added_at
   - Meta: `unique_together = ['cart', 'product']` — 同一购物车同一商品只有一行
2. `makemigrations` + `migrate`

## 验收标准

- 创建用户后可创建购物车（首次加购时）
- 同一商品不会重复创建 CartItem（unique 约束）
- CartItem.quantity 不能超过产品库存（应用层校验，不在模型层）

## Comments
