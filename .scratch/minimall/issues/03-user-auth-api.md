# 03 — 用户认证 API

> Status: `ready-for-agent` | Type: `task` | Blocked by: 02

## 目标

实现买家注册、登录、登出、个人信息查看的 REST API。

## 具体任务

1. `minimall/serializers.py`：
   - `UserRegisterSerializer` — username / email / password / payment_password (optional), 校验唯一性
   - `UserLoginSerializer` — username / password, 调用 `authenticate()` + `login()`
   - `UserMeSerializer` — 只读：id / username / email / phone / avatar / is_staff / date_joined
2. `minimall/views_buyer.py`：
   - `POST /api/auth/register/` — `APIView`
   - `POST /api/auth/login/` — `APIView`, 成功后建立 session
   - `POST /api/auth/logout/` — `APIView`, `logout(request)`
   - `GET /api/auth/me/` — `APIView`, `IsAuthenticated`
3. `minimall/urls_api.py`：挂载 `/api/auth/` 子路由

## 验收标准

- 注册 → 返回用户信息
- 登录 → 返回成功，后续请求携带 session cookie 可访问 `/api/auth/me/`
- 登出 → `/api/auth/me/` 返回 401
- 重复用户名/邮箱注册 → 返回错误

## Comments
