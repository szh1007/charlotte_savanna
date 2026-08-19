# 11 — 范围收缩: 仅支持 B 站免费视频 + 改名 BilibiliDownloader + 浅色粉蓝主题

**What to build:** 产品从「全网/万能下载」收缩为仅支持哔哩哔哩免费公开视频
（非会员、非充电）: 后端 URL 域名白名单校验 (bilibili.com 主域/子域 + b23.tv
短链, resolve 与 downloads 共用, 其他域名 422); 平台列表收窄为仅 B 站一项;
界面改名 BilibiliDownloader（中文副名「哔哩哔哩下载器」）; 深色粉紫主题改
B 站粉蓝浅色主题 (主蓝 #00AEEC + 主粉 #FB7299, 背景浅粉→浅蓝渐变, 白卡,
文字 #1F2329/#61666D); 新增 ADR-0004 与文档同步 (README/DESIGN/CONTEXT/
frontend README/.env.example); 历史记录 (bugfix/0001-0007、ADR-0001、PRD)
保留原文。

**Blocked by:** None — can start immediately

**Status:** resolved

**验收标准：**
- [x] 后端: 非哔哩哔哩域名 resolve / downloads 均返回 422 且文案含「仅支持哔哩哔哩」; bilibili.com / www. / m. / player. / b23.tv 均放行
- [x] GET /api/sites 仅返回 B 站一项, total 保留
- [x] pytest 全绿（含新增域名校验用例, YouTube/example.com 旧用例已改/删除）
- [x] 前端全站文案与品牌改名（Hero / 导航 / 页脚 / 平台墙 / meta / favicon）
- [x] 前端浅色粉蓝主题生效, 无残留深色紫粉与白字 spinner
- [x] npm run build 通过, frontend/dist/ 已重新构建
- [x] 手动验证: B 站链接解析→下载全流程可用; YouTube 链接提示「仅支持哔哩哔哩」
- [x] 文档同步（README / DESIGN / CONTEXT / frontend README / .env.example / ADR-0004）; 历史记录原文未动

## Comments

- 2026-08-20: T11 已完成实施与验证（待用户检查, 未提交 git）。核心变更: ① 新增 ADR-0004（范围收缩决策记录: 仅支持哔哩哔哩免费公开视频, 域名白名单 + KISS 理由 + 预留扩展点）; ② 后端 `schemas.py` 新增 `ensure_bilibili_url()`（urllib.parse 校验, 允许 bilibili.com 主域/子域 + b23.tv, 其余 ValueError → resolve/downloads 均 422「仅支持哔哩哔哩 (bilibili.com / b23.tv) 链接」）, resolve/downloads 路由共用; `downloader.py` 平台列表收窄为仅 B 站一项（POPULAR_SITES + 预留扩展注释）; FastAPI title 与后端包 docstring 改名; ③ 测试: 旧 YouTube/example.com 用例改 B 站, 新增 `test_domain_validation.py` 参数化 6 放行 + 5 拒绝（含 `bilibili.com.evil.com`/`b23.tv.evil.com` 子域伪造）双路由验证; ④ 前端: 全站改名 BilibiliDownloader（Hero/导航/页脚/平台墙/meta/favicon 🅱️）, 深色粉紫主题改 B 站品牌浅色粉蓝（theme.css 令牌全量映射 + 8 组件 ~30 处硬编码色, 3 处白字 spinner 改深色, 红/绿语义色保留）; ⑤ 文档同步: README/DESIGN/CONTEXT/frontend README/.env.example, PRD 加修订注记, 历史记录（bugfix/0001-0007、ADR-0001、PRD）保留原文。验证: pytest 96 passed（基线 79）、ruff 通过、npm run build 通过, curl 实测 YouTube → 422 拒绝 / b23.tv 真实解析成功 / /api/sites 仅 B 站一项（total 1751）, 残留 grep（万能/2000/极速下载/全网视频）代码零命中。遗留: 历史残留进程（uvicorn 8010 PID 896 + vite 5173 PID 22812）已由用户指示清除; 变更待用户确认后按 Conventional Commits 分块提交（ADR/issue → 后端+测试 → 前端 → 文档）。
