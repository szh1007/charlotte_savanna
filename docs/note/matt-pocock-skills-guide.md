# Matt Pocock Skills 完整使用指南

> 来源：[mattpocock/skills](https://github.com/mattpocock/skills) — GitHub 120K+ Star 的 AI Agent 技能库
>
> 核心理念：**Real Engineering, not Vibe Coding** — 用纪律化的工程工作流替代随意的 AI 编码

---

## 目录

1. [概述](#概述)
2. [安装与初始化](#安装与初始化)
3. [完整技能目录](#完整技能目录)
4. [常用技能重点讲解](#常用技能重点讲解)
   - [4.1 `/grill-me` — 需求拷问器](#41-grill-me--需求拷问器)
   - [4.2 `/grill-with-docs` — 需求拷问 + 领域建模](#42-grill-with-docs--需求拷问--领域建模)
   - [4.3 `/tdd` — 测试驱动开发](#43-tdd--测试驱动开发)
   - [4.4 `/diagnosing-bugs` — 系统化排错](#44-diagnosingbugs--系统化排错)
   - [4.5 `/to-prd` — 对话转 PRD](#45-to-prd--对话转-prd)
   - [4.6 `/to-issues` — 垂直切片式任务拆分](#46-to-issues--垂直切片式任务拆分)
   - [4.7 `/improve-codebase-architecture` — 架构改进](#47-improve-codebase-architecture--架构改进)
   - [4.8 `/prototype` — 快速原型](#48-prototype--快速原型)
5. [推荐工作流](#推荐工作流)
6. [速查表](#速查表)

---

## 概述

Matt Pocock 将真实软件工程实践（需求澄清、TDD、调试、架构治理）封装为可组合的 Agent 技能文件。每个技能是一个 Markdown 文件，被 Claude Code 等 AI 编码助手加载后遵循纪律化工作流。

### 解决的四类 AI 编码失败模式

| 失败模式 | 对应技能 |
|----------|----------|
| "Agent 没按我的意思做" | `/grill-me`、`/grill-with-docs`（通过反复对齐来消除歧义） |
| "Agent 输出过于啰嗦" | `CONTEXT.md` + `/grill-with-docs`（共享领域语言减少废话） |
| "代码跑不通" | `/tdd`（红-绿-重构）+ `/diagnosing-bugs`（系统化排查） |
| "代码变成一坨泥球" | `/improve-codebase-architecture`（深模块重构、反熵） |

---

## 安装与初始化

### 一键安装全部技能

```bash
npx skills@latest add mattpocock/skills
```

### 单独安装某个技能

```bash
npx skills@latest add mattpocock/skills/tdd
npx skills@latest add mattpocock/skills/grill-me
npx skills@latest add mattpocock/skills/diagnose
```

### 初始化（必须）

安装后在 Agent 终端中执行一次，配置 issue tracker、triage 标签、文档布局等仓库级约定：

```
/setup-matt-pocock-skills
```

当前项目的配置详情参见：
- `docs/agents/issue-tracker.md` — 本地 Markdown issue tracker
- `docs/agents/triage-labels.md` — triage 标签映射
- `docs/agents/domain.md` — 领域文档布局

---

## 完整技能目录

### 工程技能（代码工作流）

| 技能 | 调用方式 | 核心作用 |
|------|----------|----------|
| `grill-me` | `/grill-me` | 纯想法阶段的深度需求访谈，一次一个问题直到所有决策分支厘清 |
| `grill-with-docs` | `/grill-with-docs` | 有代码库时的需求对齐 + 同步更新 `CONTEXT.md` 和 ADR |
| `tdd` | Model 自动调用 | 严格红-绿-重构循环，测试未覆盖的功能一行不多写 |
| `diagnosing-bugs` | Model 自动调用 | 系统化排错：复现 → 最小化 → 假设 → 插桩 → 修复 → 回归 |
| `prototype` | Model 自动调用 | 构建一次性原型（终端应用验证逻辑 / 多 UI 变体对比） |
| `domain-modeling` | Model 自动调用 | 构建/打磨项目领域模型，挑战术语并更新 `CONTEXT.md` |
| `codebase-design` | Model 自动调用 | 共享设计词汇表（深模块、seam、adapter、locality 等） |
| `grilling` | Model 自动调用 | `grill-me` 和 `grill-with-docs` 背后的可复用访谈循环 |

### 生产力技能（非代码工作流）

| 技能 | 调用方式 | 核心作用 |
|------|----------|----------|
| `to-prd` | `/to-prd` | 将当前对话上下文直接合成为 PRD，发布到 issue tracker |
| `to-issues` | `/to-issues` | 将 PRD 按垂直切片拆分为独立可领取的 issues |
| `triage` | `/triage` | Issue 状态机管理（`needs-triage` → `ready-for-agent` → `ready-for-human`） |
| `handoff` | `/handoff` | 将当前对话压缩为接力文档，供另一个 Agent 继续工作 |
| `teach` | `/teach` | 分多次会话教用户一个新技能/概念，当前目录作为持久工作区 |
| `ask-matt` | `/ask-matt` | 路由器 — 根据当前情况推荐合适的技能或流程 |
| `writing-great-skills` | `/writing-great-skills` | 编写/编辑技能的参考文档（词汇与原则） |

### 工具/辅助技能

| 技能 | 作用 |
|------|------|
| `setup-matt-pocock-skills` | 一次性仓库配置（issue tracker、triage 标签、文档布局） |
| `git-guardrails-claude-code` | 设置 Claude Code hooks 阻止危险 git 命令（`push`、`reset --hard` 等） |
| `setup-pre-commit` | 配置 Husky pre-commit hooks（lint-staged、Prettier、类型检查、测试） |
| `scaffold-exercises` | 创建练习题目录结构（section / problem / solution / explainer） |
| `migrate-to-shoehorn` | 将 `as` 类型断言迁移到 `@total-typescript/shoehorn`（TypeScript 专用） |

---

## 常用技能重点讲解

### 4.1 `/grill-me` — 需求拷问器

**使用场景：** 只有一个模糊想法时，让 AI 反过来盘问你，直到所有决策分支厘清。

**核心机制：**
- 一次只问一个问题
- 每个问题都是对模糊分支的澄清
- 不会开始写代码，只收集信息

**典型对话示例：**

```
用户: 加一个用户头像上传功能，支持裁剪
用户: /grill-me

Claude: 在开始之前，我需要确认几点：
  1. 图片来源？—— 本地上传、URL，还是两者？
  2. 存储位置？—— 本地文件系统、S3、还是 CDN？
  3. 裁剪在前端做还是后端做？
  4. 尺寸限制？—— 最大文件大小、宽高比、输出尺寸？
  ...

回答完这些再开始。你现在只需要回答我，不需要实现。
```

**适用时机：**
- 只有一句话需求时
- 写 PRD 之前
- 多个设计决策相互依赖时
- 感觉需求有歧义但说不清哪里不对时

> **注意：** 如果项目已有代码库，作者推荐优先使用 `/grill-with-docs`，它在澄清的同时会更新领域文档。

---

### 4.2 `/grill-with-docs` — 需求拷问 + 领域建模

**使用场景：** 已有代码库时，在需求澄清过程中同步维护 `CONTEXT.md` 和 ADR（Architecture Decision Records）。

**`/grill-me` vs `/grill-with-docs` 对比：**

| | `/grill-me` | `/grill-with-docs` |
|------|-------------|---------------------|
| 适用阶段 | 纯想法、无代码 | 已有代码库 |
| 澄清需求 | ✅ | ✅ |
| 更新 CONTEXT.md | ❌ | ✅ |
| 写入 ADR | ❌ | ✅ |
| 挑战领域术语 | ❌ | ✅ |

**工作流程：**
1. 读取 `CONTEXT.md` 了解现有领域模型
2. 针对新需求逐层访谈
3. 当新概念出现时立即更新术语表
4. 当架构决策做出时写入 ADR
5. 访谈结束后，`CONTEXT.md` 和 ADR 已同步更新

**当前项目状态：** `CONTEXT.md` 和 `docs/adr/` 尚未创建，使用 `/grill-with-docs` 时会按需懒加载创建。

---

### 4.3 `/tdd` — 测试驱动开发

**使用场景：** 需要保证代码正确性、避免过度设计时。是 Matt Pocock 技能体系中最核心的开发技能。

**严格的三阶段循环：**

```
🔴 RED → 🟢 GREEN → 🔵 REFACTOR → 🔴 RED → ...
```

| 阶段 | 规则 | 说明 |
|------|------|------|
| 🔴 RED | 先写失败测试，运行确认它失败 | 不跳步——必须亲眼看到测试失败 |
| 🟢 GREEN | 写最小实现让测试通过 | **不能多写一行**——即使你知道后面还需要什么 |
| 🔵 REFACTOR | 测试全绿后才能调整结构 | 不改行为，只改结构 |

**实际示例（实现 `truncate` 函数）：**

```
用户: /tdd 实现 truncate(str, maxLen, suffix)

Claude:
  [RED] 先写测试:
    test_truncate_short_string():
      assert truncate("hi", 5) == "hi"
    test_truncate_long_string():
      assert truncate("hello world", 8) == "hello..."
  运行 → 2 FAILED ✓ 确认红灯

  [GREEN] 最小实现:
    def truncate(s, max_len, suffix="..."):
        if len(s) <= max_len:
            return s
        return s[:max_len - len(suffix)] + suffix
  运行 → 2 passed ✓ 绿灯

  [REFACTOR] 当前代码简洁，无需重构。
  是否有其他边界情况需要覆盖？
```

**关键价值：**
- Agent 不会"顺手"加防御性代码、提前抽象、过度设计
- 每个实现行都对应一个测试用例，可追溯
- 重构时有安全网，不怕改坏

**适用时机：**
- 实现新功能
- 修复 bug（先写复现测试，再修复）
- 重构（测试全绿 → 改结构 → 测试全绿）

---

### 4.4 `/diagnosing-bugs` — 系统化排错

**使用场景：** Bug 不是一眼能看出来的，需要科学排查而非猜谜。

**七步排查流程：**

```
复现 → 最小化 → 提出可证伪假设 → 插桩验证 → 修复 → 回归测试 → 清理
```

| 步骤 | 说明 |
|------|------|
| **1. 复现** | 找到稳定复现步骤，记录环境和输入 |
| **2. 最小化** | 缩减到最小可复现用例，排除无关因素 |
| **3. 假设** | 提出**可证伪**假设："如果 X 是原因，那么改变 Y 会让 bug 消失" |
| **4. 插桩** | 加日志/断点验证假设，日志统一加前缀如 `[DEBUG-20260707]` |
| **5. 修复** | 确认假设成立后，写最小修复 |
| **6. 回归** | 将复现用例转为测试，确保不再回来 |
| **7. 清理** | 搜索删除所有调试日志（统一前缀让这一步很简单） |

**回馈循环优先级（从最优到最差）：**

| 优先级 | 方式 | 反馈时间 |
|--------|------|----------|
| 🥇 | 失败测试 | 2 秒 |
| 🥈 | curl / HTTP 脚本 | 5 秒 |
| 🥉 | CLI 调用 + 快照对比 | 10 秒 |
| 4 | 无头浏览器脚本 | 30 秒 |
| 5 | 重放 trace | 分钟级 |
| 6 | 二分法定位 (git bisect) | 分钟级 |

**核心原则：假设必须可证伪。** 模糊的"我觉得问题在 X"没有价值——必须能说出"如果问题在 X，那么改 Y 会修好它，否则不是 X"。

---

### 4.5 `/to-prd` — 对话转 PRD

**使用场景：** 需求已经通过 `/grill-me` 讨论清楚，需要沉淀为正式文档。

**关键特点：**
- **不再重新采访** — 只整理对话中已有的结论
- 直接合成为结构化 PRD，发布到 issue tracker

**PRD 应包含的内容：**

```markdown
# PRD: 功能名称

## 背景与目标
为什么做这个功能？解决什么问题？

## 用户场景
谁在什么情况下使用？具体路径是什么？

## 功能边界
明确包含什么、不包含什么

## 非目标（Non-Goals）
明确排期不做的事情，防止范围蔓延

## 数据来源
数据从哪来？需要哪些 API/数据库？

## 验收标准
完成的标准是什么？如何判断功能已交付？

## 风险与开放问题
已知风险和待决策事项
```

**适用时机：**
- `/grill-me` 讨论结束后
- 需要给他人 review 需求时
- 多人协作需要统一需求理解时

---

### 4.6 `/to-issues` — 垂直切片式任务拆分

**使用场景：** PRD 写好了，需要拆成可独立开发的 tasks/issues。

**核心原则：垂直切片 > 水平切片**

```
❌ 水平切片（坏）：
  Issue 1: 建数据库表
  Issue 2: 写 API 接口
  Issue 3: 做前端页面
  Issue 4: 补测试
  → 每个 issue 只做一层，集成风险集中爆发在最后

✅ 垂直切片（好）：
  Issue 1: 用户可以查看宠物列表（schema → API → UI → 测试，一条窄路径）
  Issue 2: 用户可以按属性筛选宠物
  Issue 3: 用户可以查看单只宠物详情
  Issue 4: 用户可以编辑宠物昵称
  → 每个 issue 独立可交付、独立可测试
```

**拆分规则：**
- 每个 issue 跨 schema → API → UI → tests 的完整路径
- 每个 issue 独立可交付、独立可验证
- 按优先级排序，编号从 01 开始
- 记录依赖关系（`Blocked by: 01, 02`）

---

### 4.7 `/improve-codebase-architecture` — 架构改进

**使用场景：** 项目代码开始出现"泥球"迹象，需要系统性地识别和优化架构。

**理论基础：** John Ousterhout《A Philosophy of Software Design》的"深模块"概念 — **小接口背后隐藏大实现**。

**三阶段流程：**

| 阶段 | 说明 |
|------|------|
| **1. Explore（探索）** | Agent 遍历代码库，感知摩擦点：哪些模块"浅"（接口和实现复杂度接近）、哪些 seam 在泄漏、哪些地方需要跳很多文件才能理解一个概念 |
| **2. Present（可视化报告）** | 生成自包含 HTML 文件（Tailwind + Mermaid），为每个候选重构展示 before/after 图、问题分析、推荐强度 |
| **3. Grilling（讨论循环）** | 用户选择候选方案后，深入讨论并动态更新 `CONTEXT.md` 和 ADR |

**核心设计词汇表（来自 `codebase-design` 技能）：**

| 术语 | 定义 |
|------|------|
| **Module** | 任何有接口和实现的东西（函数、类、包、切片） |
| **Interface** | 调用者使用模块所需了解的一切（类型、不变量、错误模式） |
| **Depth** | 接口背后的行为量 — 深 = 高杠杆 |
| **Seam** | 接口所在的位置（来自 Michael Feathers） |
| **Adapter** | 在 seam 处满足接口的具体实现 |
| **Locality** | 维护者从深度中获得的好处（变更、Bug、知识集中在一处） |

---

### 4.8 `/prototype` — 快速原型

**使用场景：** 对某个设计不确定时，快速构建可丢弃的原型来验证想法。

**两种模式：**

| 模式 | 用途 | 产物 |
|------|------|------|
| **Logic prototype** | 验证状态模型/业务逻辑是否合理 | 可运行的终端应用 |
| **UI prototype** | 探索界面交互方案 | 多个可切换的 UI 变体 |

**关键原则：原型是一次性的** — 验证完就丢弃，不要在原型的烂代码上继续开发。

---

## 推荐工作流

### 按项目规模选择

| 规模 | 推荐流程 | 适用场景 |
|------|----------|----------|
| **小**（个人项目/MVP） | `/grill-me` → `/prototype` → `/tdd` | 快速验证想法 |
| **中**（团队协作） | `/grill-me` → `/to-prd` → `/to-issues` → `/prototype` → `/tdd` | 需要文档和任务拆分 |
| **大**（企业级/长期维护） | 以上全部 + `/triage` → `/diagnosing-bugs` → `/improve-codebase-architecture` | 需要架构治理 |

### Bug 修复流程

```
/triage（分类定级）→ /diagnosing-bugs（系统排查）→ /tdd（写复现测试 + 修复 + 回归）
```

### 新功能开发完整链路

```
/grill-me              →  "我们到底要做什么？"
/grill-with-docs       →  "这对现有系统意味着什么？"（有代码库时替代上一步）
/to-prd                →  "沉淀为正式需求文档"
/to-issues             →  "拆成独立可交付的垂直切片"
/prototype             →  "关键设计先验证一下"（可选）
/tdd                   →  "红-绿-重构，一个 slice 一个 slice 交付"
```

### 架构迭代流程

```
/improve-codebase-architecture  →  探索 + 可视化报告 + 讨论
  ├── /grill-with-docs          →  同步更新 CONTEXT.md 和 ADR
  ├── /prototype                →  验证新的模块接口
  └── /tdd                      →  安全重构
```

---

## 速查表

| 我想做什么 | 用哪个技能 |
|-----------|-----------|
| 把模糊想法聊清楚 | `/grill-me` |
| 聊清楚 + 维护领域文档 | `/grill-with-docs` |
| 把讨论结果写成需求文档 | `/to-prd` |
| 把需求拆成开发任务 | `/to-issues` |
| 保证代码正确、不过度设计 | `/tdd` |
| 科学排查 bug | `/diagnosing-bugs` |
| 快速验证设计想法 | `/prototype` |
| 改进代码架构 | `/improve-codebase-architecture` |
| 管理 issue 状态 | `/triage` |
| 把当前对话交给别人继续 | `/handoff` |
| 不知道用哪个 | `/ask-matt` |
| 学一个新概念/技能 | `/teach` |
| 写自己的 skill | `/writing-great-skills` |

---

## 参考资源

- [mattpocock/skills GitHub 仓库](https://github.com/mattpocock/skills)
- [Matt Pocock — A Philosophy of Software Design 相关讲座](https://www.youtube.com/@mattpocockuk)
- [John Ousterhout — A Philosophy of Software Design](https://web.stanford.edu/~ouster/cgi-bin/book.php)（深模块理论基础）
- 本项目配置：`docs/agents/issue-tracker.md`、`docs/agents/triage-labels.md`、`docs/agents/domain.md`

---

> **最后更新**：2026-07-07 | **作者**：Claude Code (charlotte)
