# 05 — JWT 认证（登录/刷新/登出）

**What to build:** 手机号+密码登录、JWT Access Token + Refresh Token、Redis 黑名单登出。

**Blocked by:** 04 — User 模型 + 注册 API

**Status:** ready-for-agent

- [ ] `miniblog/core/security.py` 新增：`create_access_token()`(15min)、`create_refresh_token()`(7天)、`decode_token()`
- [ ] 创建 `miniblog/core/deps.py`：`get_current_user()` 依赖注入（从 Authorization header 解析 token → 查用户）
- [ ] `POST /api/auth/login`：手机号+密码 → 校验 → 返回 `{access_token, refresh_token, token_type:"bearer"}`
- [ ] `POST /api/auth/refresh`：refresh_token → 校验 → 返回新 access_token
- [ ] `POST /api/auth/logout`：将 refresh_token 加入 Redis 黑名单（TTL=7天）
- [ ] 登录失败不区分"手机号不存在"和"密码错误"，统一返回"手机号或密码错误"
