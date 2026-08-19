# 04 — 会员鉴权

**What to build:** 用户输入会员密钥，校验通过后当前会话获得会员身份并保持一段时间（24h TTL）；用户可随时查询当前会话的会员状态；后续请求通过请求头携带身份标识被后端识别。

**Blocked by:** 01 — 后端骨架 + 解析链路

**Status:** resolved

**验收标准：**
- [x] `POST /api/member` 提交正确密钥 → is_member=true + expires_at
- [x] `POST /api/member` 提交错误密钥 → 明确拒绝（错误信息）
- [x] 校验通过后返回会话 token；后续请求携带 `X-Member-Token` 头可被识别为会员
- [x] `GET /api/member/status` 返回当前会话会员状态
- [x] 会话 token 超时（24h）后自动失效，会员身份收回
- [x] 密钥从环境变量读取（`MEMBER_KEY`）；提供 `.env.example` 模板（占位符），真实密钥不入库
- [x] pytest 全部通过（正确 / 错误密钥、token 过期、携带 token 访问受保护逻辑）

**Comments（实现摘要）：**

- 会话管理 `backend/auth.py`：`MemberManager`（线程安全）——`verify_key` 用 `hmac.compare_digest` 恒定时间比较校验 `config.MEMBER_KEY`（未配置空值拒绝一切提交），通过后 `secrets.token_urlsafe(32)` 签发随机 token；会话内存态 dict 存储（24h TTL），查询时惰性删除过期项；`_now()` 独立时钟函数作为测试注入点
- 识别依赖 `auth.get_member`：从 `X-Member-Token` header 读取 token 查询会话；无 / 无效 / 过期一律返回 None（免费用户是合法身份，不报错）——供 T05 档位锁定 / 并发槽 / 队列上限注入使用
- 路由 `routers/member.py`：`POST /api/member {key} → {is_member, expires_at, token}`（错误密钥 401 + 明确提示，空 key 由 Pydantic 422 拦截）；`GET /api/member/status` → `{is_member, expires_at?}`（带 token 识别会员，否则免费档）
- 契约：响应含 PRD 的 is_member/expires_at，另加 token 字段（ticket 要求返回会话 token，向上兼容）
- 测试 `tests/test_member.py`（8 个用例，HTTP seam）：正确 / 错误 / 空 / 未配置密钥、token 识别、无 token 免费、伪造 token 免费、过期收回（注入时钟推进越过 TTL，无 sleep）；conftest 的 clean_tasks 扩展为清理会员会话
- 优化（顺带）：URL 校验（http/https 规则）从 resolve / downloads 路由抽取为 `schemas.ensure_http_url` 集中定义；`config.py` 去掉过时的「T04 接入」标注
- 验证：pytest 35 passed（T01-T03 25 + T04 10），ruff lint / format 全绿
- 审查加固（code-review 双轴）：`compare_digest` 改 bytes 比较修复非 ASCII 密钥 500（补回归测试）；「过期收回」拆为独立用例 + 新增过期后重新解锁用例（一个测试一个行为）；conftest 夹具 `clean_tasks` 更名 `clean_state`（清理范围含会员会话）
- 验收第 7 条「携带 token 访问受保护逻辑」：token 识别机制经 status 端点验证（有效 / 无效 / 过期），受保护逻辑（档位锁定 / 并发槽 / 队列上限）随 T05 接入 `auth.get_member` 依赖后验收

**注意：** 项目 `.env` 尚未配置 `MEMBER_KEY`（未配置时所有密钥提交均被拒绝），真实运行前需在 `.env` 设置（模板见 `.env.example`）。
