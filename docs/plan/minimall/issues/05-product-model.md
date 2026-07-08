# 05 — 商品模型与图片

> Status: `ready-for-agent` | Type: `task` | Blocked by: 04

## 目标

定义 Product + ProductImage 模型，Admin 中注册含图片内联编辑。

## 具体任务

1. `minimall/models.py`：
   - `Product` — name, slug (unique), description, category (FK→Category), price (DecimalField), stock (PositiveIntegerField), is_active, is_featured, created_at, updated_at
   - `ProductImage` — product (FK→Product, related_name='images'), image (ImageField, upload_to='products/'), is_primary (BooleanField), sort_order
   - `Product.save()` 中自动生成 slug（如果没有手动设置）
2. `minimall/admin.py`：
   - `ProductImageInline(TabularInline)` — extra=3
   - `ProductAdmin(ModelAdmin)` — list_display: name / category / price / stock / is_active / created_at; list_filter: category / is_active; search: name / description; inlines: ProductImageInline
3. `makemigrations` + `migrate`

## 验收标准

- Admin 中新增商品时可上传多张图片
- 可设置主图
- 价格/库存字段校验（非负）

## Comments

