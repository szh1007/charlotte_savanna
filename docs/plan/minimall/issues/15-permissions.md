# 15 — 权限校验

> Status: `ready-for-agent` | Type: `task` | Blocked by: 02

## 目标

实现自定义 DRF permission class，统一管理买家 vs 管理员权限边界。

## 具体任务

`minimall/permissions.py`：

1. `IsOwnerOrAdmin`：
   - 管理员（`is_staff`）→ 直接允许
   - 资源有 `user` 字段 → 校验 `obj.user == request.user`
   - 无 user 字段的资源 → 拒绝（防止误放行）
2. `IsAdminOrReadOnly`：
   - 读（GET/HEAD/OPTIONS）→ `AllowAny`
   - 写（POST/PUT/PATCH/DELETE）→ `is_staff`

### 权限矩阵

| 视图 | permission_classes |
|------|-------------------|
| ProductListView, ProductDetailView | `AllowAny` (读); Admin 写已有 Admin 覆盖 |
| CategoryTreeView | `AllowAny` |
| Cart 全系列 | `IsAuthenticated` |
| CartItem 操作 | `IsAuthenticated` + 校验 cart 所属 |
| Order 列表/详情 | `IsAuthenticated` + 校验 user |
| Order 支付/取消/收货 | `IsAuthenticated` + 校验 order.user |
| Order 发货 (Admin action) | `is_staff` (Admin 自带) |
| Address CRUD | `IsAuthenticated` + 校验 user |

## 验收标准

- 买家访问自己的购物车 → 200
- 买家访问他人订单 ID → 404 或 403
- 管理员访问任意订单 → 200
- 匿名访问商品列表 → 200
- 匿名加购 → 401

## Comments

