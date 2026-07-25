# 36 — Vue 3 — 编辑器 & 个人中心

**What to build:** 发帖/编辑的 Markdown 编辑器、个人空间、个人中心。

**Blocked by:** 11 — 帖子发布 & 图片上传, 17 — 收藏, 20 — 浏览历史

**Status:** ready-for-agent

- [ ] `/editor` + `/editor/:id`：Markdown 编辑器（Naive UI Input type=textarea + 实时预览面板），分区下拉选择、Tags 输入、公开/私密切换、图片上传按钮（上传后光标位置插入 `![img](url)`）、草稿自动保存（localStorage）
- [ ] `/user/:id` 个人空间：用户信息卡片（头像/昵称/bio/粉丝数/关注数）+ 帖子列表（分页）+ 关注按钮
- [ ] `/profile` 个人中心：
  - 资料编辑 tab：修改昵称/bio/邮箱、上传头像、查看 credits 余额
  - 收藏 tab：收藏帖子列表
  - 浏览历史 tab：最近浏览帖子列表
  - 充值 tab：充值输入框+历史记录
  - 会话管理 tab：AI 助手历史会话列表 + 删除按钮
