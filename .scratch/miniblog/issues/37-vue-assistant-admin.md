# 37 — Vue 3 — AI 助手 & 管理后台

**What to build:** AI 问答助手页面、管理后台 9 个页面。

**Blocked by:** 23 — 公告管理, 30 — QA 端点, 31 — 管理员数据&用户, 32 — 管理员帖子&评论, 33 — 管理员流水&日志

**Status:** ready-for-agent

- [ ] `/assistant` 页面：对话界面（消息气泡 + 输入框），帖子展示卡片（Top 5，展开更多按钮），帖子摘要展示，"是否需要查询相关帖子？"追问交互，会话历史侧边栏
- [ ] `/admin` 管理后台入口：侧边栏导航 + 内容区域布局
- [ ] `/admin` 仪表盘：统计卡片（用户数/帖子数/今日新增/打赏总额）
- [ ] `/admin/users`：用户表格（搜索+筛选）、角色修改下拉、封禁/解封按钮
- [ ] `/admin/posts`：帖子表格（搜索+分区筛选+状态筛选）、强制删除按钮、修改可见性
- [ ] `/admin/comments`：评论表格（搜索+状态筛选）、强制删除按钮
- [ ] `/admin/categories`：分区 CRUD（Dialog 表单）、排序调整、指定分区管理员
- [ ] `/admin/announcements`：公告 CRUD（Dialog 表单 + 富文本）、置顶设置、时间范围选择
- [ ] `/admin/reports`：举报列表（状态筛选）、审核 Dialog（查看目标内容 + 处理/驳回）
- [ ] `/admin/transactions`：打赏流水表格 + 充值记录表格（Tab 切换）
- [ ] `/admin/logs`：操作日志表格（操作人/类型/描述/IP/时间）
- [ ] 管理后台路由守卫：仅 role=admin 或 moderator 可访问（moderator 仅可见分区管理页面）
