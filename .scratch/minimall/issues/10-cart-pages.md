# 10 — 购物车页面

> Status: `ready-for-agent` | Type: `task` | Blocked by: 09

## 目标

创建购物车页面模板，使用 Alpine.js 实现数量修改、删除、总价实时更新。

## 具体任务

1. `templates/minimall/cart.html`：extends base
   - 购物车商品列表（图片、名称、单价、数量输入框、小计、删除按钮）
   - 数量修改：Alpine.js `@input.debounce` → fetch PATCH API → 实时更新小计和总价
   - 删除按钮：`@click` → fetch DELETE → 移除行
   - 总价和商品数实时计算（Alpine.js computed）
   - "去结算"按钮 → 跳转 checkout 页
   - 空购物车状态提示
2. `minimall/views_pages.py`：
   - `CartView(TemplateView)` → `shop/cart.html`
3. `minimall/urls_pages.py`：挂载 `/shop/cart/` 路由

## 验收标准

- 购物车页面正确展示已加购商品
- 修改数量 → 总价实时更新
- 删除商品 → 列表实时刷新
- 数量输入框限制不超过库存

## Comments
