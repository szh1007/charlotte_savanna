# 01 — 基础设施搭建

> Status: `ready-for-agent` | Type: `task` | Blocked by: —

## 目标

安装 minimall 所需依赖，配置 Django settings，建立 URL 路由骨架。

## 具体任务

1. 安装 pip 依赖：`djangorestframework`, `django-redis`, `django-mptt`, `django-filter`, `Pillow`
2. 更新 `requirements.txt`（`pip freeze > requirements.txt`）
3. `charlotte_savanna/settings.py` 变更：
   - `INSTALLED_APPS` 添加：`rest_framework`, `django_filters`, `mptt`, `minimall.apps.MinimallConfig`
   - `AUTH_USER_MODEL = 'minimall.User'`
   - `MEDIA_URL` / `MEDIA_ROOT` 配置
   - `CACHES` 配置（Redis `redis://127.0.0.1:6379/1`）
   - `REST_FRAMEWORK` 配置（SessionAuthentication, PageNumberPagination, DjangoFilterBackend）
4. `charlotte_savanna/urls.py` 添加：
   - `path('api/', include('minimall.urls_api'))`
   - `path('shop/', include('minimall.urls_pages'))`
5. 更新 `.env.example` 添加 `REDIS_URL` 配置项
6. 创建 `minimall/apps.py`（`MinimallConfig`）
7. 创建空的 URL 文件：`minimall/urls_api.py`, `minimall/urls_pages.py`

## 验收标准

- `python manage.py check` 无错误
- `python manage.py runserver` 正常启动

## Comments
