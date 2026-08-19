# 04 — 会员鉴权

**What to build:** 用户输入会员密钥，校验通过后当前会话获得会员身份并保持一段时间（24h TTL）；用户可随时查询当前会话的会员状态；后续请求通过请求头携带身份标识被后端识别。

**Blocked by:** 01 — 后端骨架 + 解析链路

**Status:** ready-for-agent

**验收标准：**
- [ ] `POST /api/member` 提交正确密钥 → is_member=true + expires_at
- [ ] `POST /api/member` 提交错误密钥 → 明确拒绝（错误信息）
- [ ] 校验通过后返回会话 token；后续请求携带 `X-Member-Token` 头可被识别为会员
- [ ] `GET /api/member/status` 返回当前会话会员状态
- [ ] 会话 token 超时（24h）后自动失效，会员身份收回
- [ ] 密钥从环境变量读取（`MEMBER_KEY`）；提供 `.env.example` 模板（占位符），真实密钥不入库
- [ ] pytest 全部通过（正确 / 错误密钥、token 过期、携带 token 访问受保护逻辑）
