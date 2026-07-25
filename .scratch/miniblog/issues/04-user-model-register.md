# 04 — User 模型 + 注册 API

**What to build:** User 表、注册接口、密码 bcrypt 加密。

**Blocked by:** 02 — MySQL 引擎 + 公共基类 + Alembic

**Status:** ready-for-agent

- [ ] 创建 `miniblog/apps/user/models.py`：`User(CommonBaseModel)`，全部字段：id, phone(unique), email(unique, nullable), hashed_password, nickname, avatar_url, bio, role(enum:user/admin/moderator, default=user), credits(Decimal, default=0), follower_count, following_count, post_count, is_deleted, created_at, updated_at
- [ ] 创建 `miniblog/core/security.py`：`hash_password()` / `verify_password()`（passlib bcrypt）
- [ ] 创建 `miniblog/apps/auth/schemas.py`：`RegisterRequest`（phone + password + nickname 必填，email 可选）+ `RegisterResponse`
- [ ] `POST /api/auth/register`：校验手机号唯一 → 密码加密 → 创建 User → 返回用户信息（不含密码）
- [ ] Alembic migration 生成 + 执行
