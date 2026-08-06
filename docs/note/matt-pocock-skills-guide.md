# Matt Pocock Skills 完整使用指南

> 来源：[mattpocock/skills](https://github.com/mattpocock/skills) — GitHub 120K+ Star 的 AI Agent 技能库
>
> 核心理念：**Real Engineering, not Vibe Coding** — 用纪律化的工程工作流替代随意的 AI 编码
>
> 当前版本：2026-08（大幅重构后的 v2 版本）

---

## 目录

1. [概述](#概述)
2. [安装与初始化](#安装与初始化)
3. [技能全景图](#技能全景图)
4. [核心工作流：idea → ship](#核心工作流idea--ship)
5. [常用技能重点讲解](#常用技能重点讲解)
   - [5.1 `/grill-with-docs` — 需求拷问 + 领域建模](#51-grill-with-docs--需求拷问--领域建模)
   - [5.2 `/implement` — 按票开发](#52-implement--按票开发)
   - [5.3 `/tdd` — 测试驱动开发](#53-tdd--测试驱动开发)
   - [5.4 `/code-review` — 双轴代码审查](#54-code-review--双轴代码审查)
   - [5.5 `/to-spec` — 对话转 Spec](#55-to-spec--对话转-spec)
   - [5.6 `/to-tickets` — 垂直切片式任务拆分](#56-to-tickets--垂直切片式任务拆分)
   - [5.7 `/diagnosing-bugs` — 系统化排错](#57-diagnosingbugs--系统化排错)
   - [5.8 `/triage` — Issue 状态机管理](#58-triage--issue-状态机管理)
   - [5.9 `/wayfinder` — 大项目路线规划](#59-wayfinder--大项目路线规划)
   - [5.10 `/prototype` — 快速原型](#510-prototype--快速原型)
   - [5.11 `/improve-codebase-architecture` — 架构改进](#511-improve-codebase-architecture--架构改进)
   - [5.12 `/research` — 后台调研](#512-research--后台调研)
   - [5.13 `/handoff` — 会话接力](#513-handoff--会话接力)
6. [辅助技能](#辅助技能)
7. [推荐工作流](#推荐工作流)
8. [速查表](#速查表)

---

## 概述

Matt Pocock 将真实软件工程实践（需求澄清、TDD、调试、架构治理、项目管理）封装为可组合的 Agent 技能文件。每个技能是一个 Markdown 文件，被 Claude Code 等 AI 编码助手加载后遵循纪律化工作流。

### v2 版本的核心变化

相比旧版，2026 年的 v2 版本做了以下重大调整：

| 旧版 | 新版 | 说明 |
|------|------|------|
| `/to-prd` | `/to-spec` | 从"产品需求文档"改为"规格说明书"，更通用 |
| `/to-issues` | `/to-tickets` | 强调 tracer-bullet（曳光弹）垂直切片 |
| 无 | `/implement` | **新增**：标准化的按票开发流程，替代手动 TDD 编排 |
| 无 | `/wayfinder` | **新增**：超大型项目的路线规划工具 |
| 无 | `/research` | **新增**：后台调研，不阻塞主会话 |
| 无 | `/resolving-merge-conflicts` | **新增**：系统化解决合并冲突 |
| 无 | 大量工具类技能 | docx, pdf, pptx, xlsx 等文档处理技能 |
| 已移除 | `git-guardrails-claude-code` 等 | 部分辅助工具不再维护 |

### 核心理念：一个主流程 + 两个上匝道

v2 版本将所有技能围绕**一个主流程**组织，其他技能作为"上匝道"汇入：

```
                 ┌─ /triage（Issue 管理）
                 │
  /grill-with-docs → /to-spec → /to-tickets → /implement → /code-review
       ↑                                          │
       │               /diagnosing-bugs           │
       └───────────────（Bug 修复上匝道）──────────┘
```

---

## 安装与初始化

### 一键安装全部技能

```bash
npx skills@latest add mattpocock/skills
```

### 单独安装某个技能

```bash
npx skills@latest add mattpocock/skills/tdd
npx skills@latest add mattpocock/skills/grill-with-docs
npx skills@latest add mattpocock/skills/diagnosing-bugs
```

### 初始化（必须！）

安装后在 Agent 终端中执行一次，配置 issue tracker、triage 标签、文档布局等仓库级约定：

```
/setup-matt-pocock-skills
```

这个命令会：
- 检测你的 Git 仓库（GitHub / GitLab / 本地）
- 配置 issue tracker 类型
- 创建 `docs/agents/` 目录下的配置文件
- 在 CLAUDE.md 中添加 Agent skills 配置段落

当前项目已配置完毕，参见 `docs/agents/` 目录。

---

## 技能全景图

### 主流程技能（idea → ship）

| 技能 | 触发方式 | 一句话说明 |
|------|----------|-----------|
| `grill-with-docs` | `/grill-with-docs` | 需求访谈 + 同步维护 CONTEXT.md 和 ADR |
| `to-spec` | `/to-spec` | 将对话合成为规格说明书，发布到 issue tracker |
| `to-tickets` | `/to-tickets` | 按 tracer-bullet 垂直切片拆分为独立 tickets |
| `implement` | `/implement` | 按 ticket 开发，内置 TDD + 类型检查 + code review |
| `code-review` | 自动调用 或 `/code-review` | 双轴审查：代码规范 + 规格符合度 |

### 上匝道技能（产生工作，汇入主流程）

| 技能 | 触发方式 | 一句话说明 |
|------|----------|-----------|
| `triage` | `/triage` | Issue 状态机管理，分类→验证→grill→写 brief |
| `diagnosing-bugs` | 自动调用 或 "debug this" | 7 步科学排查法，先建反馈循环再修 |
| `wayfinder` | `/wayfinder` | 超大项目的路线规划：决策 ticket → 逐个击破 → 汇入主流程 |

### 工程基础技能（主流程和上匝道底层共用）

| 技能 | 触发方式 | 一句话说明 |
|------|----------|-----------|
| `tdd` | `/tdd` | 红-绿循环，严格按 seam 测试 |
| `grilling` | 自动调用 | 可复用的访谈循环引擎 |
| `domain-modeling` | 自动调用 | 领域术语管理，维护 CONTEXT.md |
| `codebase-design` | `/codebase-design` 或自动调用 | 深模块设计词汇表 |
| `grill-me` | `/grill-me` | 无代码库时的纯需求访谈（不写文件） |

### 独立技能

| 技能 | 触发方式 | 一句话说明 |
|------|----------|-----------|
| `prototype` | `/prototype` | 快速构建可丢弃的原型验证想法 |
| `improve-codebase-architecture` | `/improve-codebase-architecture` | 扫描代码库，生成 HTML 报告，提出深模块重构建议 |
| `research` | `/research` | 后台调研，产出带引用的 Markdown 文件 |
| `handoff` | `/handoff` | 将当前对话压缩为接力文档 |
| `resolving-merge-conflicts` | 自动调用 | 系统化解决合并冲突 |
| `teach` | `/teach` | 多会话渐进式教学 |
| `ask-matt` | `/ask-matt` | 路由器 — 不知道该用哪个技能时问它 |
| `writing-great-skills` | `/writing-great-skills` | 编写/优化 skill 的参考指南 |
| `skill-creator` | `/skill-creator` | 创建新 skill 的完整工作流（含评估和基准测试） |

### 工具/文档类技能

| 技能 | 用途 |
|------|------|
| `claude-api` | Claude API / Anthropic SDK 完整参考 |
| `doc-coauthoring` | 结构化文档协同编写（PRD、技术规格、决策记录） |
| `docx` | Word 文档创建与编辑 |
| `pdf` | PDF 读取、合并、拆分、OCR |
| `pptx` | PowerPoint 演示文稿创建与编辑 |
| `xlsx` | Excel 电子表格创建与编辑 |
| `frontend-design` | 独特、有辨识度的 UI 视觉设计指南 |
| `webapp-testing` | 用 Playwright 测试本地 Web 应用 |
| `web-artifacts-builder` | 用 React + Tailwind + shadcn/ui 构建复杂 Web 组件 |
| `mcp-builder` | MCP 服务器开发指南（Python/TypeScript） |
| `find-skills` | 搜索和发现社区 skills |
| `internal-comms` | 内部沟通文档（状态报告、事故报告等） |
| `slack-gif-creator` | 为 Slack 创建 GIF 动图 |
| `algorithmic-art` | p5.js 生成艺术 |
| `canvas-design` | PNG/PDF 静态视觉设计 |
| `brand-guidelines` | Anthropic 品牌色和排版应用 |
| `theme-factory` | 为制品应用主题样式 |
| `resolving-merge-conflicts` | 解决 git 合并冲突 |

---

## 核心工作流：idea → ship

这是 v2 版本最核心的概念。当你有一个想法想要实现时，沿着这个流程走：

### 完整路径

```
/grill-with-docs          →  "我们到底要做什么？它对现有系统意味着什么？"
        │
        ├─ (可选分支) /prototype →  "关键设计先验证一下"
        │
        ▼
/to-spec                  →  "沉淀为正式规格说明书"
        │
        ▼
/to-tickets               →  "拆成垂直切片的独立 tickets"
        │
        ▼
/implement (× N)          →  "一个 ticket 一个 ticket 交付"
        │                      每个 /implement 内部自动：
        │                        ├─ /tdd（红-绿循环）
        │                        ├─ typecheck
        │                        └─ /code-review（双轴审查）
        ▼
✅ 完成
```

### 简单任务可以跳过部分步骤

如果你的任务足够小（一个会话能搞定的），可以直接：

```
/grill-with-docs → /implement
```

跳过 `/to-spec` 和 `/to-tickets`。

### 上下文管理原则

- `/grill-with-docs` → `/to-spec` → `/to-tickets` **在同一个会话窗口**内完成（不要 compact）
- 每个 `/implement` **用全新会话**，从 ticket 文件开始
- 限制在 [smart zone](https://www.aihero.dev/ai-coding-dictionary/smart-zone)（约 120K tokens）内，超出则用 `/handoff` 接力

---

## 常用技能重点讲解

### 5.1 `/grill-with-docs` — 需求拷问 + 领域建模

**使用场景：** 有代码库时，在需求澄清过程中同步维护 CONTEXT.md 和 ADR。

**核心机制：**
- 一次只问一个问题
- 走完每个决策分支
- 当新概念出现时立即更新术语表
- 当架构决策做出时写入 ADR
- 访谈结束时，CONTEXT.md 和 ADR 已同步更新

**`/grill-me` vs `/grill-with-docs` 对比：**

| | `/grill-me` | `/grill-with-docs` |
|------|-------------|---------------------|
| 适用阶段 | 纯想法、无代码 | 已有代码库 |
| 澄清需求 | ✅ | ✅ |
| 更新 CONTEXT.md | ❌ | ✅ |
| 写入 ADR | ❌ | ✅ |
| 使用 `/domain-modeling` | ❌ | ✅（底层自动调用） |

**典型对话示例：**

```
用户: 我想给宠物领养系统加一个"收藏夹"功能
用户: /grill-with-docs

Claude: [读取 CONTEXT.md] 我看到你的系统中已有 Pet 和 Adopter 两个概念。
  第一个问题：收藏夹属于谁？是 Adopter 有一个收藏夹，还是每个 Pet 有一个"被收藏"计数？
  推荐：Adopter 持有收藏夹，因为收藏是用户行为。

用户: Adopter 持有收藏夹

Claude: [记录到 CONTEXT.md: "FavoritesList — Adopter 的子资源，存放该用户收藏的 Pet"]
  第二个问题：收藏夹是否影响领养流程？如果一只 Pet 已被其他人领养，收藏会怎样？
  ...
```

**适用时机：**
- 有代码库，需要理清新功能的影响
- 多个设计决策相互依赖时
- 希望需求讨论的结论能沉淀为文档时

---

### 5.2 `/implement` — 按票开发

**使用场景：** 有了 spec 或 ticket 后，开始写代码。这是 v2 新增的核心执行技能。

**它做什么：**
1. 读取 spec/ticket，理解要构建什么
2. 使用 `/tdd` 在预设的 seam 处编写测试和实现
3. 定期运行类型检查，单个测试文件，最后完整测试套件
4. 完成后运行 `/code-review` 审查变更
5. 提交到当前分支

**使用方式：**

```
/implement   # 在当前会话中按 ticket 开发
```

**典型工作流：**

```
用户: /implement

Claude: 我看到 ticket 是"用户可以查看宠物列表"。
  预设的测试 seam 是 PetQueryService 的公共接口。

  [RED]   先写测试：test_should_list_available_pets()
  [GREEN] 最小实现通过
  [RED]   test_should_filter_by_species()
  [GREEN] 最小实现通过

  运行 typecheck... ✅
  运行完整测试套件... ✅

  运行 /code-review...
  Standards ✅ | Spec ✅

  提交: feat(pets): add pet listing with species filter
```

> `/implement` 自带 `/tdd` — 你不需要手动在两者之间切换。它把"读 ticket → TDD → 类型检查 → review → 提交"打包成一个标准流程。

---

### 5.3 `/tdd` — 测试驱动开发

**使用场景：** 需要保证代码正确性、避免过度设计时。`/implement` 内置了它，也可以单独使用。

**严格的三阶段循环：**

```
🔴 RED → 🟢 GREEN 🔵 REFACTOR
```

| 阶段 | 规则 | 说明 |
|------|------|------|
| 🔴 RED | 先写失败测试，运行确认它失败 | 必须亲眼看到测试失败 |
| 🟢 GREEN | 写最小实现让测试通过 | **不能多写一行** — 即使你知道后面还需要什么 |
| 🔵 REFACTOR | 测试全绿后才能调整结构 | 不改行为，只改结构（重构在 code-review 阶段做） |

**v2 核心变化 — Seam 前置确认：**

写任何测试之前，必须先列出并确认 **seam**（测试接缝）：

```
Claude: 在写测试之前，我们需要确认测试 seam。
  建议的 seam: PetService 的公共方法接口
  - list_available(filters) → list[Pet]
  - get_by_id(pet_id) → Pet | None

  这些 seam 可以吗？

用户: 可以

Claude: [开始 TDD...]
```

**好的测试什么样：**
- 通过**公共接口**验证行为，不测实现细节
- 代码可以完全重写，测试不应该变
- 读起来像规格说明书："用户可以用有效购物车结算"

**不良模式：**
- **实现耦合** — mock 内部协作者、测试私有方法
- **同义反复** — `expect(add(a,b)).toBe(a+b)`，用同样的方式重算期望值
- **水平切片** — 先写所有测试再写所有实现，这是错误的

**实际示例（实现 `truncate` 函数）：**

```
用户: /tdd 实现 truncate(str, maxLen, suffix)

Claude:
  Seam 确认：truncate(text: str, max_len: int, suffix: str = "...") → str
  可以吗？
用户: 可以

Claude:
  [RED] 先写测试:
    test_short_string_unchanged():
      assert truncate("hi", 5) == "hi"
    test_long_string_truncated():
      assert truncate("hello world", 8) == "hello..."
  运行 → 2 FAILED ✓ 确认红灯

  [GREEN] 最小实现:
    def truncate(s, max_len, suffix="..."):
        if len(s) <= max_len:
            return s
        return s[:max_len - len(suffix)] + suffix
  运行 → 2 passed ✓ 绿灯
```

---

### 5.4 `/code-review` — 双轴代码审查

**使用场景：** 审查代码变更。`/implement` 结束时自动调用，也可以手动用于审查分支、PR 或任意 diff。

**两个独立的审查轴：**

| 轴 | 问题 | 含义 |
|-----|------|------|
| **Standards** | 代码是否符合规范？ | 与 `CODING_STANDARDS.md` 对比 + Fowler 代码坏味基线 |
| **Spec** | 代码是否符合需求？ | 与原始 issue / spec / PRD 逐条对比 |

两个轴用**平行 sub-agent** 审查，互不污染上下文。

**使用方式：**

```
/code-review              # 审查 HEAD 相对于 main 的变更
/code-review main         # 审查相对于 main 的变更
/code-review HEAD~5       # 审查最近 5 个 commit
/code-review <branch>     # 审查相对于某个分支的变更
```

**为什么是两个轴：**

- 代码符合所有规范但实现了错误的功能 → **Standards ✅, Spec ❌**
- 代码实现了需求但破坏了项目规范 → **Spec ✅, Standards ❌**

分开报告，一条轴不会掩盖另一条。

**Fowler 代码坏味基线（Standards 轴始终携带）：**

| 坏味 | 判断 |
|------|------|
| Mysterious Name | 名称不能揭示其用途 |
| Duplicated Code | 同一逻辑形状重复出现 |
| Feature Envy | 方法访问别人的数据比自己的多 |
| Primitive Obsession | 基础类型代替领域概念 |
| Speculative Generality | 为"可能需要"而加的抽象 |
| Shotgun Surgery | 一个逻辑变更散落在多个文件中 |

---

### 5.5 `/to-spec` — 对话转 Spec

**使用场景：** 需求已通过 `/grill-with-docs` 讨论清楚，需要沉淀为正式文档。

**关键特点：**
- **不再重新采访** — 只整理对话中已有的结论
- 直接合成为结构化 spec，发布到 issue tracker
- 自动打上 `ready-for-agent` 标签

**Spec 模板包含：**
- Problem Statement（问题陈述）
- Solution（解决方案）
- User Stories（用户故事，按 "As an <actor>, I want <feature>, so that <benefit>" 格式）
- Implementation Decisions（实现决策）
- Testing Decisions（测试决策，包含已确认的 seam）
- Out of Scope（范围外）

**使用方式：**

```
用户: /to-spec
Claude: [读取对话上下文，探索代码库]
  → 生成 spec → 确认 seam → 发布到 issue tracker
```

---

### 5.6 `/to-tickets` — 垂直切片式任务拆分

**使用场景：** Spec 已写好，需要拆成可独立开发的 tickets。

**核心原则：垂直切片 > 水平切片**

```
❌ 水平切片（坏）：
  Ticket 1: 建数据库表
  Ticket 2: 写 API
  Ticket 3: 做前端页面
  Ticket 4: 补测试
  → 每个 ticket 只做一层，集成风险集中爆发在最后

✅ 垂直切片（好）：
  Ticket 1: 用户可以查看宠物列表（schema → API → UI → test，完整窄路径）
  Ticket 2: 用户可按物种筛选宠物
  Ticket 3: 用户可查看单只宠物详情
  Ticket 4: 用户可编辑宠物昵称
  → 每个 ticket 独立可交付、独立可测试
```

**每个 ticket 声明阻塞关系（blocking edges）：**

```
Ticket 01: 宠物列表基础查询  [Blocked by: None — 可直接开始]
Ticket 02: 按物种筛选       [Blocked by: 01]
Ticket 03: 宠物详情页       [Blocked by: 01]
Ticket 04: 编辑宠物昵称      [Blocked by: 03]
```

工作前沿（frontier）：任何阻塞项已完成的 ticket 都可以开始。对于纯线性链就是从上到下。

**使用方式：**

```
用户: /to-tickets
Claude: [读取 spec → 拆分为垂直切片 → 列出给用户确认 → 发布到 tracker]
```

---

### 5.7 `/diagnosing-bugs` — 系统化排错

**使用场景：** Bug 不是一眼能看出来的，需要科学排查。

**核心原则：先建反馈循环，再写代码。没有 tight loop 之前不读代码。**

**七步排查流程：**

```
Phase 1: 构建反馈循环（最重要的阶段！）
Phase 2: 复现 + 最小化
Phase 3: 提出可证伪假设（3-5 个，排序）
Phase 4: 插桩验证
Phase 5: 修复 + 回归测试
Phase 6: 清理 + 事后复盘
```

**Phase 1 — 建立反馈循环（这个技能的灵魂）：**

按以下顺序尝试，直到有一个**能变红**（能捕获当前 bug）的命令：

| 优先级 | 方式 | 反馈时间 |
|--------|------|----------|
| 🥇 | 失败测试（在 bug 的 seam 处） | 2 秒 |
| 🥈 | curl / HTTP 脚本 | 5 秒 |
| 🥉 | CLI 调用 + 快照对比 | 10 秒 |
| 4 | 无头浏览器脚本 | 30 秒 |
| 5 | 重放 trace | 分钟级 |
| 6 | 二分法定位（git bisect） | 分钟级 |

**你必须能说出**：一个命令，你已经真正运行过，它能在这个 bug 上变红、修好后变绿。如果还没有这个命令，不要进入 Phase 2。

**Phase 3 — 假设必须可证伪：**

```
✅ 好假设: "如果数据库连接池耗尽是原因，那么增大 pool_size 会让 bug 消失，减小 pool_size 会让 bug 更容易复现"

❌ 坏假设: "我觉得问题可能在数据库那里"  ← 这是 vibe，不是假设
```

**使用方式：**

```
用户: 用户在高峰期偶尔看到 500 错误，日志里没有明显线索

Claude: [Phase 1] 让我先建一个反馈循环。
  我在测试环境用 wrk 压测 /api/pets，100 并发持续 30 秒。

  跑了 3 轮：第 2 轮和第 3 轮各出现 2 次 500。
  ✅ tight loop 建立：`wrk -c100 -d30 http://localhost:8000/api/pets`

  [Phase 2] 正在最小化...
  [Phase 3] 提出假设（排序后展示）...
```

---

### 5.8 `/triage` — Issue 状态机管理

**使用场景：** Issue 积压时，用状态机系统化地分类和处理。

**五种状态角色：**

```
needs-triage ──→ needs-info ──→ needs-triage（记者回复后）
     │
     ├──→ ready-for-agent  （AI Agent 可直接领取）
     ├──→ ready-for-human   （需要人工实现）
     └──→ wontfix           （不做）
```

**两种分类角色：**
- `bug` — 什么东西坏了
- `enhancement` — 新功能或改进

**Triage 流程（对一个 issue）：**
1. **收集上下文** — 读 issue 内容、评论、代码库
2. **推荐** — 给出分类和状态建议
3. **验证** — 对 bug 尝试复现，对 PR 确认 diff 行为
4. **Grill** — 需要细化时进行访谈
5. **应用结果** — 贴 label、写 agent brief

**常用命令：**

```
/triage                          # 显示需要关注的 issue
/triage                          # 让我看看 #42
/triage                          # 把 #42 移到 ready-for-agent
/triage                          # 有哪些 issue agent 可以直接开干？
```

> Triage 只用于**别人提交的** issue（bug 报告、外部 PR）。`/to-tickets` 产生的 tickets 已经 ready-for-agent，不需要再 triage。

---

### 5.9 `/wayfinder` — 大项目路线规划

**使用场景：** 一个模糊的超级大想法，大到一次会话装不下，从起点到终点无法一眼看穿。

**wayfinder 不是执行工具，是规划工具。** 它产出的是**决策 tickets**（问题需要回答），而不是**构建 tickets**（代码需要写）。

**核心概念：**

- **地图（Map）** — 一个 issue，打 `wayfinder:map` 标签，记录 destination、decisions、fog of war
- **决策 ticket** — 一个子 issue，类型为 `research` / `prototype` / `grilling` / `task`
- **前线（Frontier）** — 所有开放、无阻塞、未领取的 ticket
- **战争迷雾（Fog of war）** — 已知在山的那边，但还看不清具体是什么
- **范围外（Out of scope）** — 超出 destination 的工作，不会再做

**工作流程：**

```
Chart the map（绘制地图，一次会话）:
  1. 确定 destination（目的地）
  2. 广度优先的 grilling，标识开放决策
  3. 创建 map issue + 可指定的 tickets
  4. 并行启动 research tickets

Work through the map（逐个突破，多次会话）:
  每次会话：选一个 frontier ticket → 领取 → 解决 → 记录答案 → 更新地图
  永远不要在一次会话中解决多个 ticket（research ticket 除外）
```

**当路线清晰后，汇入主流程：**

```
/wayfinder 规划完成 → /to-spec 整合决策 → /to-tickets → /implement
```

---

### 5.10 `/prototype` — 快速原型

**使用场景：** 对某个设计不确定时，快速构建**可丢弃**的原型来验证想法。

**两种模式：**

| 模式 | 问题 | 产物 |
|------|------|------|
| **Logic prototype** | "这个状态模型 / 业务逻辑对吗？" | 小型交互式终端应用 |
| **UI prototype** | "这个界面应该长什么样？" | 同一路由下的多个 UI 变体 |

**核心规则：**
1. **一次性使用** — 验证完就丢弃，不要在原型的烂代码上继续开发
2. **一个命令启动** — 符合项目现有运行方式
3. **默认不持久化** — 状态在内存中
4. **不要打磨** — 不写测试、不写错误处理、不抽象
5. **捕获结论** — 将验证过的决策写入 issue 或 commit，代码留在 throwaway 分支

**在主流程中的角色：**

```
/grill-with-docs 过程中 → "这个问题纸上说不清，需要原型验证"
    → /handoff → 新会话 → /prototype → /handoff 回来
    → 带着原型的结论继续 grilling
```

---

### 5.11 `/improve-codebase-architecture` — 架构改进

**使用场景：** 代码开始出现"泥球"迹象时，系统性识别和优化架构。

**理论基础：** John Ousterhout 的"深模块"概念 — **小接口背后隐藏大实现**。

**三阶段流程：**

| 阶段 | 说明 |
|------|------|
| **1. Explore（探索）** | 从 git log 热度区域入手，感知摩擦点。重点：哪些模块浅？哪些 seam 泄露？应用 deletion test |
| **2. Present（HTML 报告）** | 生成自包含 HTML（Tailwind + Mermaid），每个候选项包含 before/after 图、问题、方案、收益、推荐强度（Strong / Worth exploring / Speculative）|
| **3. Grilling（讨论循环）** | 选一个候选方案后，深入访谈并动态更新 CONTEXT.md 和 ADR |

**核心设计词汇（来自 `codebase-design` 技能）：**

| 术语 | 定义 |
|------|------|
| **Module** | 任何有接口和实现的东西（函数、类、包、切片） |
| **Interface** | 调用者使用模块所需了解的一切（类型、不变量、错误模式） |
| **Depth** | 接口背后的行为量 — 接口小 + 实现大 = 深模块 |
| **Seam** | 接口所在的位置（来自 Michael Feathers） |
| **Adapter** | 在 seam 处满足接口的具体实现 |
| **Leverage** | 调用者从深度中获得的：一份实现，N 处受益 |
| **Locality** | 维护者从深度中获得的：变更、Bug、知识集中在一处 |

---

### 5.12 `/research` — 后台调研

**使用场景：** 需要查文档、API、规范时，不用自己慢慢读。

**它怎么做：**
1. 启动一个**后台 Agent** 去调研
2. 调研**主源**（官方文档、源码、规范），不是二手文章
3. 产出带引用的 Markdown 文件，保存到仓库
4. **你继续在主会话工作**，不用等它

**典型用法：**

```
用户: /research FastAPI 中后台任务的最佳实践，看看和 Django background tasks 的区别

Claude: [启动后台 Agent 搜索 FastAPI 文档、源码]
  → 后台运行中...你继续工作

  [几分钟后]
  → 结果保存到 docs/research/fastapi-background-tasks.md
  → 带完整引用和代码对比
```

这个文件可以作为 `/grill-with-docs` 的输入。

---

### 5.13 `/handoff` — 会话接力

**使用场景：** 当前会话满了，或者需要分支去验证什么，需要把上下文传给下一个 Agent。

**两种选择：**

| 方式 | 效果 |
|------|------|
| `/handoff` | 将对话压缩为文档，**新会话**引用该文档继续。fork，不丢失历史 |
| `/compact`（内置）| **同一会话**压缩，早期轮次被摘要。继续，但会丢失原文 |

**使用时机：**
- 上下文接近 smart zone 上限
- 需要分支出去做 prototype
- 从 prototype 回来，带回验证结论
- 从一个 developer 交给另一个 developer

---

## 辅助技能

以下技能不直接参与主流程，但在特定场景下很有用。

### `ask-matt` — 不知道该用哪个？

```
/ask-matt

→ "你的情况是什么？" → 推荐合适的技能和流程
```

### `teach` — 多会话渐进式教学

在目录中创建完整的工作区（MISSION.md、lessons、reference、learning-records），每个 lesson 是一个精美的 HTML 文件。

```
/teach 我想系统学习 Docker
```

### `writing-great-skills` — 编写/优化 Skill

如果你自己想写一个 skill，这个技能提供了完整的设计词汇和原则：信息层级、渐进披露、剪裁、leading words、失败模式等。

### `skill-creator` — Skill 工程化开发

完整的 skill 开发循环：捕捉意图 → 写草案 → 运行测试用例 → 定量评估 → 迭代优化 → 打包发布。支持基准测试和盲比。

### `find-skills` — 发现社区 Skills

```
"有没有处理 CSV 文件的 skill？"
→ 搜索 skills.sh leaderboard → 推荐 → 安装
```

### `resolving-merge-conflicts` — 系统化解冲突

1. 查看冲突状态
2. 找到每个冲突的来源（commit message、PR、原始 issue）
3. 保留双方意图，不兼容时选匹配合并目标的
4. 运行自动化检查（typecheck → tests → format）
5. 完成合并/变基

### `claude-api` — Claude API 完整参考

当你的代码需要调用 Claude API 时自动触发。涵盖模型 ID、定价、参数、streaming、tool use、MCP、agents、prompt caching 等。

### 文档类技能速查

| 需求 | 技能 |
|------|------|
| 写 Word 文档 | `docx` |
| 处理 PDF | `pdf` |
| 做 PPT | `pptx` |
| 做 Excel | `xlsx` |
| 做 UI 设计 | `frontend-design` |
| 测试 Web 应用 | `webapp-testing` |
| 构建复杂 Web 组件 | `web-artifacts-builder` |
| 协同写文档 | `doc-coauthoring` |

---

## 推荐工作流

### 按项目规模选择

| 规模 | 推荐流程 |
|------|----------|
| **小**（一个小时能搞定的功能） | `/grill-with-docs` → `/implement` |
| **中**（需要几个会话的功能） | `/grill-with-docs` → `/to-spec` → `/to-tickets` → `/implement` × N |
| **大**（跨多个 milestone 的功能） | 完整流程 + `/wayfinder` → `/to-spec` → `/to-tickets` → `/implement` × N |
| **超大**（看不到终点的探索性项目） | `/wayfinder`（先做路线规划）→ 路线清晰后汇入主流程 |

### Bug 修复流程

```
/diagnosing-bugs（建 loop → 复现 → 假设 → 修复 → 回归）
    ├── 如果 bug 的根本原因是架构问题
    └── → /improve-codebase-architecture
```

### Issue 管理流程

```
/triage（看一看有什么需要关注的）
    ├── needs-triage → grilling → ready-for-agent
    └── ready-for-agent → /implement
```

### 架构迭代流程

```
/improve-codebase-architecture  →  探索 + HTML 报告 + 选候选
  ├── /grill-with-docs          →  同步更新 CONTEXT.md 和 ADR
  ├── /prototype                →  验证新的模块接口
  └── /implement                →  安全重构（TDD + review）
```

---

## 速查表

| 我想做什么 | 用哪个技能 |
|-----------|-----------|
| 把模糊想法聊清楚（有代码库） | `/grill-with-docs` |
| 纯想法阶段、没有代码库 | `/grill-me` |
| 把讨论结果写成需求文档 | `/to-spec` |
| 把需求拆成开发任务 | `/to-tickets` |
| 按 ticket 开发（TDD + review 自动打包） | `/implement` |
| 保证代码正确、不过度设计（手动控制） | `/tdd` |
| 审查代码变更 | `/code-review` |
| 科学排查 bug | `/diagnosing-bugs`（说"debug this"即可触发） |
| 管理 issue 状态 | `/triage` |
| 超大项目的路线规划 | `/wayfinder` |
| 快速验证设计想法 | `/prototype` |
| 改进代码架构 | `/improve-codebase-architecture` |
| 后台查资料 | `/research` |
| 把当前对话交给下一个会话继续 | `/handoff` |
| 学一个新概念/技能 | `/teach` |
| 不知道用哪个 | `/ask-matt` |
| 写自己的 skill | `/skill-creator` 或 `/writing-great-skills` |
| 调用 Claude API | `claude-api`（自动触发） |
| 解决合并冲突 | `resolving-merge-conflicts`（自动触发） |
| 写文档（PRD、设计文档） | `/doc-coauthoring` |

---

## 参考资源

- [mattpocock/skills GitHub 仓库](https://github.com/mattpocock/skills)
- [Matt Pocock — AI Coding Dictionary](https://www.aihero.dev/ai-coding-dictionary)
- [John Ousterhout — A Philosophy of Software Design](https://web.stanford.edu/~ouster/cgi-bin/book.php)（深模块理论基础）
- [Michael Feathers — Working Effectively with Legacy Code](https://www.goodreads.com/book/show/44919.Working_Effectively_with_Legacy_Code)（Seam 概念来源）
- [skills.sh](https://skills.sh/) — 社区 skill 市场
- 本项目配置：`docs/agents/issue-tracker.md`、`docs/agents/triage-labels.md`、`docs/agents/domain.md`

---

> **最后更新**：2026-08-06 | **作者**：Claude Code (charlotte)
