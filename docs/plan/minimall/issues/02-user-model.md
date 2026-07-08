# 02 — 用户模型

> Status: `ready-for-agent` | Type: `task` | Blocked by: 01

## 目标

自定义 `AbstractUser`，添加 minimall 所需的买家字段，配置为项目用户模型。

## 具体任务

1. `minimall/models.py`：定义 `User(AbstractUser)`
   - 新增字段：`phone`（CharField, nullable）, `avatar`（ImageField, nullable）, `payment_password`（CharField, nullable, 存储哈希）
   - 复用 `is_staff` 作为管理员标识
   - 复用 `is_active` 作为账号启用标识
2. `minimall/managers.py`：`UserManager`（`BaseUserManager`），`create_user` / `create_superuser`
3. `makemigrations minimall` + `migrate`
4. 确保 `AUTH_USER_MODEL = 'minimall.User'` 已在 settings 中（issue 01）

## 验收标准

- `python manage.py makemigrations minimall` 生成迁移文件，无错误
- `python manage.py migrate` 执行成功
- `python manage.py createsuperuser` 可创建管理员

## Comments

