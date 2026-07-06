# CLAUDE.md — 系统级配置

> Claude Code 系统级上下文文件，适用于所有项目。项目特定内容请在各项目的 `CLAUDE.md` 中维护

> 参考标准：Google Technical Writing、PEP 8 / PEP 257、Conventional Commits、SOLID、12-Factor App、Karpathy-skills

---

## 1. 沟通与回复规范

### 1.1 语言规则

- **中文**：解释、分析、讨论、注释
- **英文**：代码标识符、CLI 命令、变量名、函数名、文件路径、技术术语
- 混用时不给英文术语加中文引号或翻译

```
# ✅ 正确
在 settings.py 中把 DEBUG 设为 False

# ❌ 错误
在 设置.派 中把 调试 设为 假
```

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

### 2.1 Python (PEP 8 + Google Python Style Guide)

**格式：**
- 缩进：4 空格，禁止 Tab
- 行宽：120 字符（Django 项目）/ 88 字符（Black formatter 项目）/ 79 字符（标准库风格）— 匹配项目现有风格
- 编码：UTF-8，换行符：Unix `\n`
- 文件末尾：有且仅有一个空行

**命名 (PEP 8)：**

| 类型 | 规则 | 示例 |
|------|------|------|
| 模块/文件 | `snake_case` | `document_loader.py` |
| 类 | `PascalCase` | `HttpResponse`, `UserProfile` |
| 函数/方法 | `snake_case` | `get_user_by_id()` |
| 变量 | `snake_case` | `user_list`, `is_active` |
| 常量 | `UPPER_SNAKE_CASE` | `MAX_RETRY_COUNT`, `BASE_DIR` |
| 私有成员 | `_leading` / `__mangling` | `_cache`, `__secret` |

**类型注解 (PEP 484 / PEP 604)：**
```python
# Python 3.10+：使用 | 替代 Optional / Union
def get_user(user_id: int) -> dict[str, str] | None:
    ...

# 复杂类型使用 TypeAlias
from typing import TypeAlias
UserData: TypeAlias = dict[str, int | str | bool]
```

**文档字符串 (PEP 257 + Google Style)：**
```python
def process(path: str, timeout: int = 30) -> Result | None:
    """处理指定路径的文件。

    Args:
        path: 文件路径。
        timeout: 超时秒数，默认 30。

    Returns:
        处理结果；文件不存在时返回 None。

    Raises:
        ValueError: path 为空时抛出。
    """
```

**Import 顺序 (isort 风格)：**
1. 标准库 (`import os, sys, json`)
2. 第三方库 (`from django.conf import settings`)
3. 本地模块 (`from .models import User`)

每组之间空一行，按字母序排列。

### 2.2 JavaScript / TypeScript

- ESLint + Prettier，遵循项目现有配置
- 优先 `const`，需要重新赋值时用 `let`，禁用 `var`
- 使用箭头函数作为回调，async/await 优于 Promise chain
- TypeScript：严格模式，避免 `any`，优先 interface 而非 type

### 2.3 Shell 脚本

- `#!/usr/bin/env bash` 开头
- `set -euo pipefail` 确保错误时退出
- 变量引用加双引号：`"$VAR"`
- 用 `[[ ]]` 而非 `[ ]`（bash 专有）

### 2.4 通用架构原则 (SOLID + DRY)

- **S**ingle Responsibility：一个类/函数只做一件事
- **O**pen/Closed：对扩展开放，对修改封闭
- **L**iskov Substitution：子类可以替换父类而不出错
- **I**nterface Segregation：接口最小化，不强制依赖无用方法
- **D**ependency Inversion：依赖抽象而非具体实现
- **DRY** (Don't Repeat Yourself)：重复 ≥ 3 次时抽取
- **KISS** (Keep It Simple)：优先简单方案，不过度设计
- **YAGNI** (You Aren't Gonna Need It)：不为"以后可能需要"提前实现

---

## 3. 安全规范

> 参考：OWASP Top 10、12-Factor App (Config)、CWE Top 25。

### 3.1 敏感信息管理（最高优先级）

- **绝不提交**：`.env`、API Key、Token、密码、私钥、证书
- **从环境变量读取**：敏感配置一律通过 `os.environ.get()` 或等效方式
- **提供模板**：项目应有 `.env.example` 列出所需变量（值用 `your-xxx-here` 占位）
- **检查残留**：提交前确认代码注释中无真实 Key — 注释中的演示 Key 用占位符 `sk-your-api-key`

### 3.2 框架安全

- Django：`DEBUG=False`（生产）、`SECRET_KEY` 从环境变量读取、CSRF 保护保持开启
- Flask/FastAPI：关闭 debug 模式、设置合理的 CORS 策略
- 数据库：参数化查询，禁止字符串拼接 SQL
- 认证：不自行实现加密算法，使用框架内置或经过审计的库

### 3.3 .gitignore 检查清单

```gitignore
# 必备项
.env
*.local
*.secret
*.pem
*.key
credentials.*
# 框架特定
db.sqlite3
/media/
# IDE
.idea/
.vscode/
```

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

遵循 Conventional Commits，英文优先：

```
<type>: <imperative description>

feat: add user authentication
fix: resolve token refresh race condition
refactor: extract validation middleware
docs: update API error codes
chore: bump dependencies
test: add auth middleware coverage

# 英文无法简洁表达时允许中文：
feat: add 用户权限管理 module
```

- 首行 ≤ 72 字符
- 祈使语气（"add" 非 "added"）
- 不加重叠信息（文件列表在 diff 里已有）
- 不需要句末标点

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

### 5.1 测试层级

| 层级 | 覆盖目标 | 工具示例 |
|------|----------|----------|
| 单元测试 | 函数/方法逻辑 | pytest, unittest, Jest |
| 集成测试 | 模块间交互、数据库 | pytest + fixtures, Testcontainers |
| E2E 测试 | 关键用户路径 | Playwright, Cypress, Selenium |

### 5.2 编写原则

- AAA 模式：Arrange（准备） → Act（执行） → Assert（断言）
- 一个测试只验证一个行为
- 测试名称描述行为，不描述实现：`test_returns_401_when_token_expired` ✓
- 不依赖测试执行顺序，每个测试独立
- Mock 外部服务（API、数据库），不 Mock 自己的代码

### 5.3 运行要求

- 提交前运行相关测试套件
- 新增功能应包含对应测试
- 修复 bug 应先写能复现的失败测试

---

## 6. 开发约定

### 6.1 环境管理

- Python 项目：使用 `.venv/`（项目根目录下），`python -m venv .venv`
- 依赖文件：`requirements.txt` 或 `pyproject.toml`，提交到仓库
- Node 项目：使用项目本地 `node_modules/`，不全局安装
- 环境变量：`.env` 不提交，`.env.example` 提交到仓库

### 6.2 注释规范

- 解释"为什么"（设计决策、workaround 原因），而非"是什么"（代码已经说明了是什么）
- 中文注释，简洁直白
- TODO/FIXME/HACK 标记应附带上下文：
  ```python
  # TODO(2026-08): 切换到 httpx 后移除此 workaround
  # HACK: Django 6.0.x 下 QuerySet.aggregate() 返回类型不一致
  ```

### 6.3 错误处理

- 明确的异常类型，不泛用 `except Exception`
- 异常消息包含足够的上下文用于排查
- 库代码抛出明确的自定义异常，应用代码在边界层统一处理
- 不静默吞异常 — 至少记录日志

### 6.4 日志

- 使用标准 logging 模块，不直接 `print()`
- 级别：DEBUG（开发调试）→ INFO（关键流程节点）→ WARNING（异常但可恢复）→ ERROR（需人工介入）
- 日志消息包含关键上下文（如 user_id、request_id），不记录敏感信息

### 6.5 性能意识

- 避免 N+1 查询（Django ORM 用 `select_related` / `prefetch_related`）
- 大数据量操作使用分页或流式处理
- 不提前优化 — 先保证正确，再 profile 找到真正瓶颈

---

## 7. 项目 CLAUDE.md 约定

每个项目可在 `.claude/CLAUDE.md` 中维护项目特定内容：

| 内容 | 位置 | 说明 |
|------|------|------|
| 项目概述 | 项目级 | 用途、背景、目标 |
| 技术栈 | 项目级 | 具体版本和提供商 |
| 目录结构 | 项目级 | 关键文件和目录说明 |
| 框架特定规范 | 项目级 | 如 Django / LangChain / React 约定 |
| 开发状态 | 项目级 | 当前进度、待办事项 |
| 项目注意事项 | 项目级 | 特定于本项目的提醒 |

项目级 CLAUDE.md 不应重复本文件已有的通用内容（沟通规范、Git 规范、安全规范等），只需在项目开头引用即可：

```markdown
> 通用规范参见系统级 CLAUDE.md（~/.claude/CLAUDE.md）。
> 本文档仅包含 charlotte_savanna 项目特定内容。
```

---

## 8. LLM 编码行为准则 (Karpathy's SOTA Guidelines)

> 来源：https://github.com/multica-ai/andrej-karpathy-skills

> 聚焦 LLM 编码时常见的错误倾向，偏保守 — 简单任务可灵活处理。

### 8.1 先想后写 (Think Before Coding)

**不要假设，不要隐藏困惑，主动暴露权衡。**

实现之前：
- 明确陈述你的假设。如果不确定，直接问。
- 如果存在多种解读，全部列出 — 不要默默选一个。
- 如果有更简单的方案，直接指出。必要时 push back。
- 如果哪里不清楚，停下来，说清楚困惑点，提问。

### 8.2 简单优先 (Simplicity First)

**用最少代码解决问题。不写投机代码。**

- 不实现用户没要求的功能。
- 不为单次使用的代码创建抽象。
- 不添加未被要求的"灵活性"或"可配置性"。
- 不为不可能发生的场景写错误处理。
- 200 行能解决的问题写了 500 行 → 重写。

自问："资深工程师会说这过度设计吗？" 如果是，简化。

### 8.3 精准修改 (Surgical Changes)

**只动必须动的。只清理自己造成的烂摊子。**

编辑已有代码时：
- 不"顺便优化"相邻代码、注释或格式。
- 不重构没有坏的东西。
- 匹配现有风格，即使你觉得自己的写法更好。
- 如果注意到无关的死代码，口头提及 — 不删除。

当你的修改造成了孤立代码（未使用的 import/变量/函数）：
- 清理被**你的修改**孤立的部分。
- 不删除之前就存在的死代码，除非明确要求。

检验标准：每个被改动的行都应追溯到用户的需求。

### 8.4 目标驱动执行 (Goal-Driven Execution)

**定义成功标准。循环直到验证通过。**

把任务转化为可验证的目标：
- "加个校验" → "先写无效输入测试，再让测试通过"
- "修 bug" → "先写能复现的测试，再让它通过"
- "重构 X" → "确保前后测试都通过"

多步任务先列简要计划：
```
1. [步骤] → 验证: [检查项]
2. [步骤] → 验证: [检查项]
3. [步骤] → 验证: [检查项]
```

强成功标准让你能自主循环推进。弱标准（"把它修好"）需要持续确认。

---

**这些准则生效的标志：** diff 中不必要的改动减少、因过度设计导致的重写减少、澄清性问题在实现之前提出而非出错之后。

---

> **最后更新**：2026-07-06 | **维护者**：Claude Code
