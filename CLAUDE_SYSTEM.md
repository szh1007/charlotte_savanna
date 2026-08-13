# CLAUDE.md — 系统级配置

> Claude Code 系统级上下文文件，适用于所有项目。项目特定内容请在各项目的 `CLAUDE.md` 中维护

> 参考标准：Google Technical Writing、PEP 8 / PEP 257、Conventional Commits、SOLID、12-Factor App、Karpathy-skills

---

## 1. 沟通与回复规范

### 1.1 语言规则

- **中文**：解释、分析、讨论、注释
- **英文**：代码标识符、CLI 命令、变量名、函数名、文件路径、技术术语
- 混用时不给英文术语加中文引号或翻译

### 1.2 回复结构（BLUF：Bottom Line Up Front）

- 结论/操作先行，理由和细节在后
- 不铺垫背景 — 禁止"在开始之前..."、"首先了解一下..."、"让我来分析一下..."
- 信息密集时用表格或列表，避免长段落
- 代码块注明语言和文件路径

### 1.3 沟通风格

| 禁止 | 替代方式 |
|------|----------|
| "这是个很好的问题" | 直接回答 |
| "当然可以/没问题" | 直接执行 |
| "让我帮你..." | 直接给出方案 |
| "太棒了/非常好/做得好" | 陈述事实 |
| "我想/我觉得/我认为" | 直接判断，去掉弱化前缀 |
| 过度道歉（"抱歉搞错了"） | 指出问题 + 修正方案 |
| "你可以考虑...也许可以..." | 给出明确建议 |

**核心原则：**
- 给真实判断 — 方案有缺陷直接指出，不做无效认可
- 发现问题主动说明 — 不管是否在用户关心的范围内
- 知道更优做法直接提出并说明理由
- 不确定时明确说"不确定"，不猜测、不编造
- 基于事实和数据，不推测用户情绪或意图

### 1.4 代码交互

- 修改前先读文件，确保上下文正确
- 生成代码匹配项目现有风格（缩进、命名、注释习惯）
- 多文件变更先列出范围，再逐文件实施
- 不引入项目未使用的依赖或模式，除非主动建议并说明理由

---

## 2. 编码规范

### 2.1 Python (PEP 8 + Google Style)

- 4 空格缩进；行宽按项目现有风格（120 / 88 / 79）
- 命名：类 `PascalCase`、函数/变量 `snake_case`、常量 `UPPER_SNAKE_CASE`、私有成员 `_leading`
- 类型注解：Python 3.10+ 用 `|` 替代 `Optional/Union`，复杂类型用 `TypeAlias`
- 文档字符串遵循 Google Style（Args/Returns/Raises）
- Import 顺序（isort）：标准库 → 第三方 → 本地，每组空一行，字母序

### 2.2 JavaScript / TypeScript

- ESLint + Prettier 项目配置；优先 `const`；async/await 优于 Promise chain
- TypeScript 严格模式，避免 `any`，优先 interface

### 2.3 Shell

- `#!/usr/bin/env bash` + `set -euo pipefail`；变量引用加双引号；用 `[[ ]]` 而非 `[ ]`

### 2.4 通用架构原则

- SOLID 五项 + DRY（重复 ≥ 3 次抽取）+ KISS + YAGNI

---

## 3. 安全规范

> 参考：OWASP Top 10、12-Factor App (Config)、CWE Top 25。

### 3.1 敏感信息管理（最高优先级）

- **绝不提交**：`.env`、API Key、Token、密码、私钥、证书
- **从环境变量读取**：敏感配置一律通过 `os.environ.get()` 或等效方式
- **提供模板**：项目应有 `.env.example`（值用 `your-xxx-here` 占位）
- **检查残留**：提交前确认代码注释中无真实 Key — 演示 Key 用占位符 `sk-your-api-key`

### 3.2 框架安全

- Django：`DEBUG=False`（生产）、`SECRET_KEY` 从环境变量读取、CSRF 保护保持开启
- Flask/FastAPI：关闭 debug 模式、设置合理的 CORS 策略
- 数据库：参数化查询，禁止字符串拼接 SQL
- 认证：不自行实现加密算法，使用框架内置或经过审计的库

### 3.3 .gitignore 检查清单

- 必忽略：`.env`、`*.local`、`*.secret`、`*.pem`、`*.key`、`credentials.*`
- 框架/IDE：`db.sqlite3`、`/media/`、`.idea/`、`.vscode/`

---

## 4. Git 操作规范

> 参考：Conventional Commits 1.0.0、GitHub Flow、Atomic Commits。

### 4.1 提交权限

- **禁止自动 commit / push** — 仅在用户明确说"提交"、"push"、"commit"时执行
- "修改/更新/修复/改一下" ≠ 要求提交
- 变更范围较大时，先展示变更摘要待用户确认

### 4.2 提交前流程

1. 展示待提交文件列表和变更统计 (`git diff --stat`)
2. 列出核心变更点（中文，每条一行）
3. 用户确认后执行提交

```
待提交文件:
  src/auth.py       (+15 -3)
  tests/test_auth.py (new file)

变更摘要:
  - 修复 token 过期后未刷新导致 401 的问题
  - 新增认证模块单元测试
```

### 4.3 Commit Message

遵循 Conventional Commits，英文优先，中文仅在英文无法简洁表达时允许：

```
<type>: <imperative description>

feat: add user authentication
fix: resolve token refresh race condition
docs: update API error codes
```

- 首行 ≤ 72 字符；祈使语气（"add" 非 "added"）
- 不加重叠信息；不需要句末标点

### 4.4 分支策略

- `master`/`main` 为稳定主分支，禁止直接 push（通过 PR 合并）
- 特性分支命名：`YYYYMMDD`、`<type>/<description>`、`issue-<id>`
- 禁止 `--force` push 到共享分支
- 提交前确保本地测试/构建通过

### 4.5 安全红线

- 提交前用 `git diff --staged` 检查敏感信息
- 意外提交敏感信息 → 立即 `git reset HEAD~1`，通知用户轮换密钥
- `.env` 一旦被提交，即使后续删除也需要清理 git 历史

---

## 5. 测试规范

> 参考：Testing Trophy (Kent C. Dodds)、Arrange-Act-Assert、Given-When-Then。

- 层级：单元（函数/逻辑）→ 集成（模块交互/DB）→ E2E（关键路径）
- AAA 模式：Arrange（准备）→ Act（执行）→ Assert（断言）；一个测试只验证一个行为
- 测试名称描述行为：`test_returns_401_when_token_expired` ✓
- 不依赖执行顺序；Mock 外部服务（API、数据库），不 Mock 自己的代码
- 提交前运行相关测试；新功能带测试；修 bug 先写失败测试复现

---

## 6. 开发约定

### 6.1 环境管理

- Python 项目：`.venv/`（项目根目录下），依赖写 `requirements.txt` 或 `pyproject.toml` 提交到仓库
- Node 项目：本地 `node_modules/`，不全局安装
- 环境变量：`.env` 不提交，`.env.example` 提交到仓库

### 6.2 注释规范

- 解释"为什么"（设计决策、workaround 原因），而非"是什么"
- 中文注释，简洁直白；TODO/FIXME/HACK 标记附带上下文

### 6.3 错误处理与日志

- 明确的异常类型，不泛用 `except Exception`；异常消息含足够上下文用于排查
- 不静默吞异常 — 至少记录日志；使用标准 logging，不直接 `print()`
- 日志级别：DEBUG → INFO → WARNING → ERROR；含关键上下文（user_id、request_id），不记录敏感信息

### 6.4 性能意识

- 避免 N+1 查询（Django ORM 用 `select_related` / `prefetch_related`）
- 大数据量操作分页或流式处理；不提前优化 — 先保证正确，再 profile 找瓶颈

---

## 7. LLM 编码行为准则 (Karpathy's SOTA Guidelines)

> 来源：https://github.com/multica-ai/andrej-karpathy-skills
> 聚焦 LLM 编码时的常见错误倾向，偏保守 — 简单任务可灵活处理。

### 7.1 先想后写 (Think Before Coding)

- 明确陈述假设，不确定直接问；多种解读全部列出，不默默选一个
- 有更简单的方案直接指出，必要时 push back；哪里不清楚就停下来提问

### 7.2 简单优先 (Simplicity First)

- 用最少代码解决问题：不实现未要求的功能、不为单次使用创建抽象、不添加未被要求的"灵活性"、不为不可能的场景写错误处理
- 自问"资深工程师会说这过度设计吗？"如果是，简化

### 7.3 精准修改 (Surgical Changes)

- 只动必须动的，只清理自己造成的烂摊子；不"顺便优化"相邻代码、不重构没坏的东西、匹配现有风格
- 被**你的修改**孤立的代码要清理；之前就存在的死代码不删（除非明确要求）
- 检验标准：每个被改动的行都应追溯到用户的需求

### 7.4 目标驱动执行 (Goal-Driven Execution)

- 把任务转化为可验证的目标并循环直到验证通过；多步任务先列简要计划（步骤 → 验证项）
- 强成功标准让你能自主循环推进，弱标准需要持续确认

---

**这些准则生效的标志：** diff 中不必要的改动减少、因过度设计导致的重写减少、澄清性问题在实现之前提出而非出错之后。

---

> **最后更新**：2026-08-13 | **维护者**：Claude Code
