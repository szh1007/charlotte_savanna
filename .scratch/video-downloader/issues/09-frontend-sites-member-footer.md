# 09 — 前端平台墙 + 会员区 + 页脚

**What to build:** 用户浏览平台支持墙了解服务能力；在会员营销区看到免费 / 会员功能对比与密钥输入入口，输入正确密钥后解锁会员能力（锁定档位标识消失）；页面底部展示版权免责声明。

**Blocked by:** 04 — 会员鉴权, 07 — 前端工程 + 主题 + Hero 解析区

**Status:** resolved

**验收标准：**
- [x] 平台墙：卡片网格（每平台 icon + 名称 + 支持格式），数据来自平台接口，粉彩描边 hover 上浮动画
- [x] 会员营销区（深色对比块）：免费 / 会员功能对比表（清晰度 / 并发 / 队列 / 有效期）+「限时」营销元素 + 密钥输入框 + 解锁提示动画
- [x] 输入正确密钥 → 解锁成功反馈 + 全站会员状态生效（🔒 锁定标识消失、免费限制文案更新）
- [x] 输入错误密钥 → 明确错误提示
- [x] 会员状态持久于当前会话（刷新页面后仍可通过状态接口恢复）
- [x] 页脚：版权免责声明（仅个人学习使用 / 不破解 DRM / 封号风险自担 / 尊重版权）
- [x] 浏览器逐块验收通过

## Comments

- 2026-08-19: T09 完成。实现: 后端 /api/sites 契约扩展 (POPULAR_SITES 每平台增加 formats 字段, list_sites 透传, test_health_sites 断言更新) + client.js 自动附加 X-Member-Token (localStorage 持久化 vd_member_token, 使解析/下载按会员身份计算) + submitMemberKey/fetchMemberStatus。组件: PlatformWall.vue (接口数据 12 平台卡片网格, icon+名称+格式, 粉彩描边 hover 上浮) + MemberSection.vue (深色渐变描边对比块: 免费/会员 4 项能力对比表 + 「✦ 限时特惠」脉冲 badge + 距今日 24:00 每秒倒计时 + 密钥输入框 + 解锁成功发光动画 + 错误密钥 401 detail 透传; 已解锁态展示权益与过期时间) + SiteFooter.vue (4 条免责声明: 个人学习/不破解 DRM/封号风险自担/尊重版权) + useMember.js composable (解锁/恢复/清 token 与解析下载逻辑解耦)。Home.vue 整合: 会员状态全站共享, 解锁成功自动重新解析 (后端按会员身份返回无锁定档位, 🔒 消失), NavBar 按钮接通 (未解锁滚动到会员区并聚焦密钥输入框, 已解锁实心渐变「✓ 会员已解锁」), 结果卡 member_limited 时新增「🔓 解锁会员档位」引导按钮 (US 35 转化路径)。验证: `npm run build` 通过 (81.22KB JS); 后端 pytest 53 passed 回归; 浏览器 E2E 12 项全过 (Chrome headless + CDP 直驱真实后端: 平台墙 12 卡与接口一致 → 错误密钥「密钥无效」→ 免费态真实解析 2 档 🔒 → 引导按钮滚动到会员区 → 正确密钥解锁 → NavBar 状态切换 → 自动重解析 🔒 2→0 (1080p/best 可选) → 刷新后会员状态恢复 → 页脚 4 条免责 → 375px 视口无溢出)。/code-review 双轴审查通过, 修复: downloader.py YouTube 行 91 字符超 88 (ruff E501) 折行 / US 35 引导按钮 (原锁定档 disabled 无转化路径) / 解锁重解析失败丢旧结果卡 (handleResolve 支持 keepOld, 失败保留旧卡) / 会员默认档位回落最低档 360p (改反向找最高可用档) / Home.vue 职责膨胀 (会员逻辑抽 useMember.js) / document.getElementById 字符串耦合 (改组件 ref $el)。提交: `aa32113`。
