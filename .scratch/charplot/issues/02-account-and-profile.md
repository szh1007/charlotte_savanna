# 02 — 账号体系与个人主页

**Status:** done

**Blocked by:** 01 — 三端骨架与健康检查

**What to build:** 用户可注册 / 登录 / 登出，登录后自动创建 charplot_profile（游戏化状态载体）。个人主页展示等级 / XP / 连胜 / 最大连胜 / 心动值 / 学习币，含基础统计面板（登录天数 / 已答题数等，T05 后有数据自然流入）与学习币兑换连胜冻结入口。连胜中断时展示损失警告。

**Acceptance criteria:**
- [x] 注册 → 登录 → 登出全流程可用，登录后 profile 自动创建
- [x] 个人主页展示等级 / XP / 连胜 / 最大连胜 / 心动值 / 学习币，字段与 profile 数据一致
- [x] 学习币兑换连胜冻结接口可用（币为 0 时不可兑换，前端给出引导）
- [x] 连胜中断逻辑：跨日未学习时展示损失警告
- [x] 登录事件落库（用户事件表），登录天数统计可查
- [x] 管理员（is_staff）与普通用户权限区分建立

**Skills:** 前端部分开发时使用 `/frontend-design` 技能（视觉设计规范与主题落地）

**References:** DESIGN.md §7 步骤 02；PRD A-1/A-2/A-3、G-2、G-4；SPEC §8/§9

---

## Comments

### 2026-08-22 实施完成（Claude Code）

**已确认决策（用户拍板）：**

- API 路径前缀 `/api/charplot/...`（与骨架一致；尾斜杠风格）
- 冻结规则：10 学习币 / 冻结 1 天，可叠加顺延，常量集中配置（`services.py`）
- 中断判定：`profile.last_study_date` 非空且距今 > 1 天且未在冻结期 → 警告；last_study_date 由 Issue 05 更新

**交付物：**

| 端 | 路径 | 说明 |
|----|------|------|
| Django | `app/charplot/` | `CharplotUserEvent` 用户事件表（login/level_clear/answer，按日 get_or_create 去重，JSON payload 预留）；`CharplotProfile` 新增 `last_study_date` / `freeze_until`；`services.py` 冻结兑换 / 中断警告 / 登录天数统计；`permissions.py` IsStaff；迁移 0002 已应用 |
| API | `/api/charplot/auth/{session,register,login,logout}/` + `/profile/` + `/profile/streak-freeze/` | SessionAuthentication；注册/登录显式 `csrf_protect`（DRF 默认豁免 CSRF，防 login CSRF）；登录事件显式落库（不用 signal，避免 admin 登录污染统计） |
| 配置 | `settings/dev.py` | `CSRF_TRUSTED_ORIGINS`（Vite 代理 Origin 校验关键配置） |
| 前端 | `project/charplot/frontend/` | vue-router（/login /register /profile + 守卫）；client.ts 统一 request（CSRF cookie → X-CSRFToken + Content-Type + 错误归一）；stores/auth.ts；Login/Register/Profile 视图 + 导航徽章组（Lv/连胜/心/币）；签名元素：连胜火焰呼吸动画 |
| 测试 | `app/charplot/tests/` | 44 个（test_api 全流程 + CSRF 锁 + test_services 规则 + test_permissions） |

**验证结果（2026-08-22 实测）：**

- `manage.py test app.charplot` 44 个 OK；ruff / pre-commit 全绿；`vue-tsc` + `vite build` 通过
- Playwright 全流程：注册 → 登录 → 个人主页字段一致（登录天数 1，事件落库）→ 冻结兑换（25→15→5 币，叠加顺延 8/23→8/24）→ 币不足禁用 + 引导 → 连胜警告 banner（2 天未学习）→ 兑换后冻结横幅切换 → 刷新会话保持 → 登出 → is_staff「管理员」徽章；0 console error

**排坑记录：**

| 坑 | 处理 |
|----|------|
| 前端 POST 415 | fetch 需显式 `Content-Type: application/json`（DRF 按头选解析器） |
| 注册 400 密码太常见 | 测试密码避开 Django 常见密码验证器（`pass1234` → `TestPass#2026`） |
| Vite 端口 3001 EACCES | Windows Hyper-V/WSL 端口排除范围（2910-3009），迁移前端到 **4300**（vite.config + FastAPI CORS + CSRF_TRUSTED_ORIGINS + Home.vue 同步） |
| DRF 视图 CSRF 默认豁免 | `as_view` 返回 `csrf_exempt(view)`，未认证登录/注册端点无 CSRF 保护 → 显式 `@method_decorator(csrf_protect)` |
| 兑换后警告不消失 | store 兑换后需重新拉取 profile（streak_loss_warning 是后端计算字段） |
