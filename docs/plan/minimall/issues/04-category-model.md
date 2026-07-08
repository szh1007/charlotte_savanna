# 04 — 商品分类模型

> Status: `ready-for-agent` | Type: `task` | Blocked by: 01

## 目标

使用 django-mptt 实现多层商品分类，在 Django Admin 中注册。

## 具体任务

1. `minimall/models.py`：定义 `Category(MPTTModel)`
   - `name` (CharField), `slug` (SlugField, unique), `parent` (TreeForeignKey), `is_active` (BooleanField)
   - MPTT 自动管理 `lft`, `rght`, `tree_id`, `level`
   - `class MPTTMeta`: `order_insertion_by = ['name']`
2. `minimall/admin.py`：
   - `CategoryAdmin(MPTTModelAdmin)` — list_display: name / slug / is_active, search: name
3. `makemigrations` + `migrate`

## 验收标准

- Admin 中可创建多级分类（如 "电子产品 > 手机 > iPhone"），树形展示
- 通过 `Category.get_descendants()` 可获取子分类
- 分类 slug 唯一

## Comments

