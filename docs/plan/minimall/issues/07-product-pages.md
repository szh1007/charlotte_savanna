# 07 — 商品页面模板

> Status: `ready-for-agent` | Type: `task` | Blocked by: 06

## 目标

创建买家端商品相关 Django 模板页面，使用 HTMX 实现局部刷新交互。

## 具体任务

1. `templates/minimall/base.html`：基础布局
   - 顶部导航栏（Logo、分类导航、搜索框、购物车角标、登录/注册/用户名）
   - footer
   - Alpine.js + HTMX CDN 引入
2. `templates/minimall/index.html`：extends base
   - 推荐商品卡片列表
   - 分类导航区域
3. `templates/minimall/product_list.html`：extends base
   - 左侧分类树（点击触发 HTMX 筛选）
   - 搜索框（输入防抖触发 HTMX 请求）
   - 商品卡片网格（hx-target="#product-grid"）
   - 价格区间筛选
   - 排序选择
4. `templates/minimall/product_detail.html`：extends base
   - 商品大图 + 缩略图切换
   - 名称、价格、库存、描述
   - 加入购物车按钮（Alpine.js `@click` → fetch POST API）
   - 数量选择器
5. `templates/minimall/partials/`：
   - `product_card.html` — 单个商品卡片（HTMX 列表刷新用）
   - `cart_badge.html` — 购物车角标数字（Alpine.js 状态）
6. `minimall/views_pages.py`：
   - `IndexView(TemplateView)` → `shop/index.html`
   - `ProductListView(TemplateView)` → `shop/product_list.html`
   - `ProductDetailView(TemplateView)` → `shop/product_detail.html`
7. `minimall/urls_pages.py`：挂载 `/shop/` 子路由

## 验收标准

- 访问 `/shop/` 显示首页
- 点击分类导航 → 跳转商品列表并筛选
- 搜索框输入关键词 → 列表刷新
- 商品详情页 → 加入购物车按钮触发 API 请求

## Comments

