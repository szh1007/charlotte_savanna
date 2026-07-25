# miniblog 博客社区 — 产品需求文档 (PRD)

> Status: `ready-for-agent` | 创建：2026-07-26 | 项目根目录：`/miniblog`

---

## 1. Problem Statement

用户需要一个完整的博客社区系统，支持用户发布帖子、社区互动、智能内容推荐与问答检索。项目定位为 **FastAPI 全栈学习项目**，以 `/miniblog` 为根目录独立构建，前后端分离架构。

### 1.1 用户角色

| 角色 | 标识 | 描述 |
|------|------|------|
| 匿名用户 | 未登录 | 浏览社区广场帖子、查看帖子详情、关键词搜索 |
| 注册用户 | 已登录，`role=user` | 发帖、评论、点赞、打赏、收藏、关注、个人空间管理、AI 问答 |
| 分区管理员 | 已登录，`role=moderator` | 管理指定分区的帖子与评论 |
| 系统管理员 | 已登录，`role=admin` | 全站用户/帖子/评论/分区/公告/举报/操作日志管理 |

---

## 2. Solution

基于 **FastAPI + MySQL + Redis + Celery + LangChain + Milvus + Vue 3** 的全栈博客社区。

### 2.1 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 后端框架 | FastAPI (async) | REST API |
| ORM | SQLAlchemy 2.0 async + aiomysql | MySQL 数据库操作 |
| 迁移 | Alembic | 数据库 schema 管理 |
| 认证 | JWT (Access 15min + Refresh 7天) + Redis 黑名单 | token 认证 |
| 缓存 | Redis | 帖子缓存、热门排行(ZSET)、限流 |
| 异步任务 | Celery + Redis broker | 统计更新、embedding 同步、通知 |
| 向量数据库 | Milvus | 帖子 embedding 存储与相似度检索 |
| LLM | DeepSeek V4 Pro | 智能问答 + Agent |
| Embedding | text-embedding-3-large (3072维) | 帖子/查询向量化 |
| Agent 记忆 | PostgresSaver | LangGraph checkpoint 持久化 |
| 数据库 | MySQL (业务数据) + PostgreSQL (Agent 会话) | 双数据库 |
| 前端 | Vue 3 + Vite + Naive UI + Tailwind CSS | SPA 前后端分离 |
| 密码加密 | passlib / bcrypt | 密码哈希存储 |
| 邮件 | smtplib + email | 密码重置邮件 |

### 2.2 中间件依赖

| 中间件 | 用途 | 连接配置 |
|------|------|----------|
| MySQL | 业务数据（用户、帖子、评论等） | `DB_USERNAME/PASSWORD/HOST/PORT/DB_NAME_FASTAPI=miniblog` |
| PostgreSQL | LangGraph Agent 会话 (PostgresSaver) | `PG_DB_USERNAME/PASSWORD/HOST/PORT/PG_DB_NAME_FASTAPI=miniblog` |
| Redis | 缓存 + Celery broker + 限流 | `REDIS_URL=redis://127.0.0.1:6379/1` |
| Milvus | 帖子向量存储 | `MILVUS_URL=http://localhost:19530` |

---

## 3. User Stories

### 3.1 认证与用户

1. 作为一个新用户，我希望使用手机号+密码注册账号，以便加入社区。
2. 作为一个已注册用户，我希望使用手机号+密码登录，以便访问个人功能。
3. 作为一个已登录用户，我希望我的 token 过期后能用 refresh token 自动续期，以便保持登录状态。
4. 作为一个忘记密码的用户，我希望通过邮箱接收重置链接来重设密码。
5. 作为一个管理员，我希望通过邮箱登录，以便与普通用户区分。
6. 作为一个已登录用户，我希望编辑我的个人资料（昵称、头像、bio、邮箱），以便完善个人信息。
7. 作为一个已登录用户，我希望在个人中心查看我的充值记录。

### 3.2 帖子与社区

8. 作为一个已登录用户，我希望使用 Markdown 编辑发布帖子到我的个人空间，并可选择是否公开可见。
9. 作为一个已登录用户，我希望发帖时选择帖子所属分区（科技/影视/动画/音乐/游戏/生活/番剧/知识/体育），以便帖子被正确归类。
10. 作为一个已登录用户，我希望给帖子添加标签（tags），以便其他用户按标签搜索。
11. 作为一个已登录用户，我希望编辑我发布的帖子内容和可见性。
12. 作为一个已登录用户，我希望我的帖子被删除时不会影响已有数据追溯（软删除）。
13. 作为一个匿名用户/已登录用户，我希望在社区广场按分区浏览所有公开帖子。
14. 作为一个用户，我希望通过关键词搜索帖子的标题和内容，以便快速找到感兴趣的内容。
15. 作为一个已登录用户，我希望看到我的浏览历史中最近 N 条看过的帖子（去重，保留最新访问时间）。

### 3.3 评论

16. 作为一个已登录用户，我希望对帖子发表评论。
17. 作为一个已登录用户，我希望回复他人的评论，显示为"回复xxx："格式。
18. 作为一个用户，我希望看到帖子的所有评论按时间排序展示（两层扁平结构）。
19. 作为一个评论者，我希望我的评论被删除时不会影响已有数据追溯（软删除）。

### 3.4 互动功能

20. 作为一个已登录用户，我希望给帖子点赞，也可以取消点赞（toggle）。
21. 作为一个已登录用户，我希望打赏帖子作者（虚拟 credits），余额不足时弹窗提示是否充值。
22. 作为一个已登录用户，我希望在充值弹窗中输入 credits 数量，点击确认后完成模拟充值。
23. 作为一个已登录用户，我希望收藏帖子，也可以取消收藏（toggle）。
24. 作为一个已登录用户，我希望关注其他用户，查看我的粉丝数和关注数。
25. 作为一个已登录用户，我希望我的帖子收到评论/点赞/打赏/关注时收到通知（铃铛 badge 显示未读数）。

### 3.5 举报

26. 作为一个已登录用户，我希望举报违规的帖子或评论。
27. 作为一个管理员，我希望审核举报列表，对举报执行处理（删除目标/驳回举报）。

### 3.6 AI 智能助手

28. 作为一个已登录用户，我希望能用自然语言向 AI 助手提问。如果我**明确要求找帖子**，系统展示 Top 5 相关帖子（支持展开到最多 20 篇，相关性 > 0.6），并对每篇帖子生成内容摘要。如果**没有明确要求找帖子**，系统仅展示 Top 2 高相关帖子并回答问题，最后追问是否需要查询相关帖子。
29. 作为一个已登录用户，我希望社区广场的帖子按我的偏好排序（后期基于 LangChain 用户偏好向量实现）。

### 3.7 管理员

30. 作为一个管理员，我希望在管理后台查看全站数据概览（用户数、帖子数、今日新增等）。
31. 作为一个管理员，我希望管理用户（列表查看、封禁/解封、修改角色）。
32. 作为一个管理员，我希望管理帖子（列表查看、删除、修改可见性）。
33. 作为一个管理员，我希望管理评论（列表查看、删除违规评论）。
34. 作为一个管理员，我希望管理分区（新增、编辑、删除、指定分区管理员）。
35. 作为一个管理员，我希望发布系统公告（标题、内容、置顶、生效时间范围）。
36. 作为一个管理员，我希望查看所有用户的打赏流水和充值记录。
37. 作为一个管理员，我希望查看管理员操作日志，以便追溯关键操作。

---

## 4. Implementation Decisions

### 4.1 架构决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 项目结构 | 分层结构 (`core/apps/tasks/llm/`) | 17 个模型、功能模块多，按业务领域拆分避免单文件膨胀 |
| 前后端 | 完全分离，独立项目 | Vue 3 独立开发部署，FastAPI 纯 API |
| 删除策略 | 全局软删除 | 便于数据追溯与举报关联 |
| 评论层级 | 2 层扁平：一级评论 + 回复（`parent_id` + `reply_to_user_id`） | 避免深层嵌套，UI 友好 |
| 关注模式 | 单向关注（微博模式） | 博客社区标准做法 |
| 分区设计 | 动态分区表 + 管理员可增删改 | 灵活扩展，不改代码 |
| 草稿保存 | 前端 localStorage | 不需要跨设备，降低服务端复杂度 |
| 接口限流 | Redis 实现（登录 60s/5次 等） | 防暴力破解和刷接口 |

### 4.2 数据库模型（17 个）

| # | 模型 | 所属模块 | 关键字段 |
|---|------|----------|----------|
| 1 | `User` | user | id, phone, email, hashed_password, nickname, avatar_url, bio, role(enum:user/admin/moderator), credits, follower_count, following_count, post_count, is_deleted, created_at, updated_at |
| 2 | `Category` | category | id, name, slug, description, icon, sort_order, is_active, moderator_id(FK→User), created_at, updated_at |
| 3 | `Post` | post | id, title, content(Markdown), author_id(FK→User), category_id(FK→Category), is_public, view_count, like_count, comment_count, tip_count, tip_total, collection_count, heat_score, is_deleted, created_at, updated_at |
| 4 | `PostTag` | post | id, post_id(FK→Post), tag_name(索引) — 帖子标签关联 |
| 5 | `Comment` | comment | id, post_id(FK→Post), user_id(FK→User), parent_id(FK→self, nullable), reply_to_user_id(FK→User, nullable), content(Markdown), like_count, is_deleted, created_at |
| 6 | `Collection` | collection | id, user_id, post_id — 联合唯一(user_id, post_id) |
| 7 | `BrowseHistory` | history | id, user_id, post_id, visited_at — 联合唯一(user_id, post_id)，重复更新 visited_at |
| 8 | `Like` | like | id, user_id, post_id — 联合唯一(user_id, post_id) |
| 9 | `Tip` | tip | id, from_user_id, to_user_id, post_id, amount, created_at |
| 10 | `RechargeRecord` | tip | id, user_id, amount, created_at |
| 11 | `Follow` | follow | id, follower_id, followed_id — 联合唯一(follower_id, followed_id) |
| 12 | `Notification` | notification | id, receiver_id, sender_id, type(enum:like/comment/reply/follow/tip), target_type(enum:post/comment), target_id, is_read, is_deleted, created_at |
| 13 | `Announcement` | announcement | id, title, content, publisher_id(FK→User), is_pinned, effective_from, effective_to, is_deleted, created_at |
| 14 | `Report` | report | id, reporter_id(FK→User), target_type(enum:post/comment), target_id, reason, status(enum:pending/resolved/dismissed), handler_id(FK→User, nullable), created_at, resolved_at |
| 15 | `OperationLog` | admin | id, operator_id(FK→User), action_type(enum:delete_post/ban_user/change_role 等), target_desc, ip_address, created_at |
| 16 | `PasswordResetToken` | auth | id, user_id, token(JWT), expires_at, is_used |
| 17 | 通用基类 | core | `CommonBaseModel`：id, created_at, updated_at — 所有模型继承 |

### 4.3 统计冗余字段与热度公式

帖子表冗余 `view_count`, `like_count`, `comment_count`, `tip_count`, `tip_total`, `collection_count`, `heat_score`。用户表冗余 `follower_count`, `following_count`, `post_count`。全部由 Celery 异步更新。

**热度公式**：`heat_score = view_count × 1 + like_count × 2 + comment_count × 1.5 + collection_count × 3`

权重通过环境变量 `HEAT_VIEW_WEIGHT` / `HEAT_LIKE_WEIGHT` / `HEAT_COMMENT_WEIGHT` / `HEAT_COLLECTION_WEIGHT` 配置。

### 4.4 API 响应格式

统一格式：

```json
{
  "code": 1,
  "message": "success",
  "data": { ... } | [ ... ] | null,
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 156,
    "total_pages": 8
  }
}
```

| code | 含义 |
|------|------|
| `1` | 成功 |
| `0` | 业务错误（余额不足、密码错误等） |
| `-1` | 认证失败 / Token 过期 |

列表接口统一带 `pagination`，单条数据接口不带。

### 4.5 认证流程

- 手机号 + 密码登录（主），邮箱备用
- 登录成功返回 `access_token`(15min) + `refresh_token`(7天)
- `refresh_token` 换取新 `access_token`
- 登出时将 `refresh_token` 加入 Redis 黑名单
- 密码重置：用户输入邮箱 → 生成 30min 有效 JWT → Celery 发邮件 → 用户点链接重置 → token 加入黑名单
- passlib / bcrypt 加密所有密码

### 4.6 缓存策略

| 场景 | 策略 | TTL |
|------|------|-----|
| 热门帖子排行 | Celery Beat 定时计算 + Redis ZSET | 5 分钟刷新 |
| 帖子详情 | `@cache` 装饰器，访问时刷新 TTL | 10 分钟 |
| 用户主页帖子列表 | 用户维度缓存 | 5 分钟 |
| 社区广场帖子列表 | 分页缓存 | 2 分钟 |
| 接口限流 | Redis 计数器 + 滑动窗口 | 按接口配置 |

### 4.7 Celery 任务清单

| # | 任务 | 触发 | 优先级 |
|---|------|------|--------|
| 1 | 帖子统计更新（like/comment/collection/view count） | 用户操作后 | 高 |
| 2 | 用户统计更新（post/follower/following count） | 用户操作后 | 高 |
| 3 | 帖子的 tip_count/tip_total 更新 | 打赏完成后 | 高 |
| 4 | 通知发送 | like/comment/reply/follow/tip | 高 |
| 5 | 帖子 embedding 生成 + 写入 Milvus | 发布/编辑帖子 | 中 |
| 6 | 帖子删除 → Milvus 移除 embedding | 删除帖子 | 中 |
| 7 | 帖子可见性变更 → 缓存失效 + Milvus 同步 | 公开↔私密切换 | 中 |
| 8 | 密码重置邮件发送 | 用户请求重置 | 中 |
| 9 | 热门帖子排行刷新（ZSET + heat_score） | 定时 5min | 低 |
| 10 | 热门帖子缓存更新 | 定时 | 低 |
| 11 | 用户注销/封禁 → 清理 PG checkpoint | 注销/封禁 | 低 |
| 12 | 管理后台数据概览预计算 | 定时 | 低 |

### 4.8 LangChain 智能助手

**LLM**：DeepSeek V4 Pro（`deepseek-v4-pro`，`extra_body={"thinking": {"type": "disabled"}}`）

**Embedding**：`openai:text-embedding-3-large`，维度 3072，通过 OPENAI_API_KEY + OPENAI_BASE_URL 调用

**向量存储**：Milvus（`MILVUS_URL=http://localhost:19530`，`metric_type=COSINE`，db=`charlotte`，collection=`docs`）

**Agent 记忆**：PostgresSaver（LangGraph checkpoint），thread_id 隔离会话，用户可管理自己的会话列表

**问答逻辑**：
- 用户提出问题 → Agent 判断意图
- **明确要求找帖子**：Milvus 检索 → 返回 Top 5 帖子 → 展开按钮可加载到 20 篇（相关性 > 0.6）→ 逐帖生成内容摘要
- **没有明确要求找帖子**：Milvus 检索 → 仅 Top 2（相关性极高） → 回答用户问题 → 追问是否需要查询相关帖子

**智能推荐（后期）**：
- Celery 定时任务将帖子（标题+正文+标签）做 embedding 写入 Milvus
- 根据用户浏览/收藏/点赞记录生成用户偏好向量 → 向量相似度检索 → 高分帖子前置展示

### 4.9 打赏与充值流程

**打赏（同步事务）**：
1. 用户点击打赏 → 输入 credits 数量 → 确认
2. 校验余额 → 不足时弹窗提示是否充值
3. 余额充足：同事务内执行 扣减(A) → 增加(B) → 写入 Tip 流水 → 提交
4. Celery 异步更新帖子的 tip_count / tip_total

**充值（同步）**：
1. 弹窗输入 credits 数量 → 确认
2. 写入 RechargeRecord → 更新 User.credits

### 4.10 举报流程

1. 用户点击帖子/评论旁的"举报"按钮 → 选择原因 → 提交
2. 生成 Report 记录（status=pending）
3. 管理员在后台审核 → 处理（删除目标 post/comment 并 status=resolved）或驳回（status=dismissed）

### 4.11 软删除

全局软删除策略：所有可删除实体（User/Post/Comment）均使用 `is_deleted` 字段标记，API 层自动过滤 `is_deleted=True` 的记录。管理员可在后台查看已删除内容用于审核追溯。

### 4.12 文件上传

- 头像：`/miniblog/uploads/avatars/`，单文件上传
- 帖子图片：`/miniblog/uploads/posts/`，上传后返回 Markdown 图片语法，前端编辑器插入光标位置

### 4.13 前端路由

**用户端（11 个页面）**：

| 路由 | 页面 |
|------|------|
| `/` | 首页/社区广场 |
| `/login` | 登录 |
| `/register` | 注册 |
| `/reset-password` | 密码重置 |
| `/post/:id` | 帖子详情 |
| `/editor` | 写帖子（Markdown） |
| `/editor/:id` | 编辑帖子 |
| `/user/:id` | 用户个人空间 |
| `/profile` | 个人中心（资料/充值记录/浏览历史/收藏/会话管理） |
| `/category/:slug` | 分区帖子列表 |
| `/search` | 搜索结果 + AI 助手入口 |
| `/assistant` | AI 问答助手 |

**管理端（9 个页面）**：

| 路由 | 页面 |
|------|------|
| `/admin` | 数据概览 |
| `/admin/users` | 用户管理 |
| `/admin/posts` | 帖子管理 |
| `/admin/comments` | 评论管理 |
| `/admin/categories` | 分区管理 |
| `/admin/announcements` | 公告管理 |
| `/admin/reports` | 举报审核 |
| `/admin/transactions` | 打赏/充值流水 |
| `/admin/logs` | 操作日志 |

### 4.14 项目目录结构

```
miniblog/
├── main.py                  # FastAPI 入口 + lifespan
├── config.py                # 配置（环境变量读取）
├── core/
│   ├── database.py          # MySQL 引擎 + AsyncSession
│   ├── pg_database.py       # PostgreSQL 引擎（PostgresSaver）
│   ├── security.py          # JWT + passlib/bcrypt
│   ├── deps.py              # 公共依赖注入（get_db, get_current_user）
│   ├── cache.py             # Redis 缓存工具 + @cache 装饰器
│   └── exceptions.py        # 全局异常处理
├── apps/
│   ├── auth/                # 认证模块（login/register/token/reset-password/rate-limit）
│   ├── user/                # 用户模块（profile/avatar/recharge/logout）
│   ├── post/                # 帖子模块（CRUD/image-upload）
│   ├── category/            # 分区模块
│   ├── comment/             # 评论模块
│   ├── collection/          # 收藏模块
│   ├── browse_history/      # 浏览历史模块
│   ├── like/                # 点赞模块
│   ├── tip/                 # 打赏+充值模块
│   ├── follow/              # 关注模块
│   ├── notification/        # 通知模块
│   ├── report/              # 举报模块
│   ├── announcement/        # 公告模块
│   ├── search/              # 关键词搜索模块
│   └── admin/               # 管理员模块（概览/管理/操作日志）
├── tasks/                   # Celery 异步任务
│   ├── celery_app.py        # Celery 应用实例
│   ├── stats.py             # 统计更新任务
│   ├── embedding.py         # Milvus embedding 同步任务
│   ├── notification.py      # 通知发送任务
│   ├── email.py             # 邮件发送任务
│   └── cache_refresh.py     # 缓存刷新定时任务
├── llm/                     # LangChain 智能助手
│   ├── agent.py             # Agent 初始化（DeepSeek + PostgresSaver）
│   ├── embedding.py         # Embedding 模型初始化
│   ├── retriever.py         # Milvus 检索器
│   └── recommendation.py    # 智能推荐（后期）
├── schemas/                 # Pydantic 公共 schemas
│   └── response.py          # 统一响应格式
├── alembic/                 # 数据库迁移
│   ├── env.py
│   └── versions/
├── uploads/                 # 文件上传目录
│   ├── avatars/
│   └── posts/
└── tests/                   # 测试
    ├── conftest.py
    ├── test_auth/
    ├── test_post/
    └── ...
```

---

## 5. Testing Decisions

### 5.1 测试原则

- 只测试外部可观察行为，不测试实现细节
- AAA 模式：Arrange → Act → Assert
- 每个测试独立，不依赖执行顺序
- Mock 外部服务（Redis/Milvus/Celery），不 Mock 自己的 Service 层

### 5.2 测试范围

| 层级 | 范围 | 工具 |
|------|------|------|
| 单元测试 | Service 层业务逻辑、工具函数 | pytest |
| 接口测试 | 所有 API 端点（认证/CRUD/边界条件） | pytest + httpx (AsyncClient) |
| 集成测试 | 关键业务流程（注册→发帖→评论→打赏 全链路） | pytest |

### 5.3 测试环境

- SQLAlchemy 使用 SQLite 内存数据库隔离测试
- Redis/Milvus/Celery 使用 mock 或 test fixtures
- 测试数据库每次用例后自动清理

---

## 6. Out of Scope

本次**不包含**：

- 真实短信验证码（需第三方 API，个人项目成本高）
- 真实支付对接（微信/支付宝）
- 跨设备草稿同步（仅 localStorage）
- 消息实时推送（WebSocket）
- 国际化（i18n）
- 移动端适配
- 部署与 CI/CD
- 缓存管理后台手动刷新功能（暂时不做）
- 前端自动化测试（E2E）

---

## 7. Further Notes

### 7.1 启动依赖顺序

1. MySQL（端口 3306，数据库 `miniblog`）
2. PostgreSQL（端口 5432，数据库 `miniblog`）
3. Redis（端口 6379）
4. Milvus（端口 19530，执行 `standalone_embed.bat` 启动）
5. Celery Worker（`celery -A miniblog.tasks.celery_app worker --loglevel=info`）
6. Celery Beat（`celery -A miniblog.tasks.celery_app beat --loglevel=info`）
7. FastAPI（`uvicorn miniblog.main:app --reload`）
8. 前端（`npm run dev`）

### 7.2 环境变量

实施时将新增以下环境变量到 `.env` 和 `.env.example`，并标注 miniblog 项目归属：

- `FASTAPI_SECRET_KEY` — JWT 签名密钥
- `DB_NAME_FASTAPI` — MySQL 业务数据库名
- `PG_DB_USERNAME/PASSWORD/HOST/PORT/PG_DB_NAME_FASTAPI` — PostgreSQL 连接
- `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` — Celery Redis 连接
- `MILVUS_URL/DATABASE/COLLECTION` / `EMBEDDING_DIM=3072` — Milvus
- `DEEPSEEK_API_BASE/KEY` / `DEEPSEEK_MODEL` — LLM
- `EMBEDDING_MODEL` — text-embedding-3-large
- `ACCESS_TOKEN_EXPIRE_MINUTES=15` / `REFRESH_TOKEN_EXPIRE_DAYS=7`
- `HEAT_VIEW/LIKE/COMMENT/COLLECTION_WEIGHT` — 热度权重
- `SMTP_HOST/PORT/USER/PASSWORD` — 邮件服务

### 7.3 demo 参考

项目 ORM 参考 `demo/FastAPI/demo3_orm.py`（SQLAlchemy 2.0 async + aiomysql）。
LangChain RAG 参考 `demo/LangChain_20260714/_10_RAG/10_5_RAG.py`（Milvus + Agent + PostgresSaver）。
Embedding 参考 `demo/LangChain_20260714/_10_RAG/10_3_RAG_embedding.py`（text-embedding-3-large）。
向量存储参考 `demo/LangChain_20260714/_10_RAG/10_4_RAG_vecto_store.py`（Milvus DDL/DQL）。

---

## 8. 变更记录

| 日期 | 变更 | 作者 |
|------|------|------|
| 2026-07-26 | 初始版本（grilling + domain-modeling） | Claude Code (charlotte) |
