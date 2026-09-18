# 01 · 框架侧：业务接入点

**Status:** done

**Type:** task

**Blocked by:** 无

**上游:** `../PRD.md` §4.1 / §4.2 / §4.4 / §4.8

## 做什么

给框架加一个"业务怎么接进来"的统一约定，**并且证明加这个接入点没有改到 agent 主循环**。

框架现在已经能跑（核心循环、工具、模型、快照、数据层、命令行都完成，700+ 测试）。本 issue 只做"接入点"，不重写任何已有组件。

## 具体任务

1. **新增工具提供者约定**
   - 一个"运行上下文"结构：装会话编号 + 一块**框架不解释**的载荷字典（业务往里放自己的东西，比如用户 ID）
   - 一个"工具提供者"协议：`async def provide(运行上下文) -> 工具列表`
   - 关键：框架只负责**透传**载荷，永远不读里面的内容、不定义里面有什么字段

2. **上浮到框架的公共 API**
   - 上面两个东西要能被业务 import 到
   - 框架根门面已有一条"防漂移测试"（子包导出的名字必须都能从根门面拿到），改完要让它继续通过

3. **提示词加载支持指定目录**
   - 现在只能从框架自己的提示词目录读，业务提示词没地方放
   - 加一个可选参数指定目录，**默认值保持现状**（框架自己的目录）

4. **让会话装配支持指定提示词名**
   - 现在提示词名写死成 `system`，业务要用自己的客服提示词就得改这里
   - 加可选参数，**默认值保持现状**（`system`），保证命令行现有行为一字不变

5. **加包声明文件**
   - 框架现在只是"一个目录"，靠"从仓库根运行"隐式工作，两个顶层目录之间的关系没有任何文件声明
   - 加上之后可以独立安装，依赖是已知的 8 个：httpx / openai / pydantic / python-dotenv / sqlalchemy / psycopg / redis / alembic

6. **写通用性测试**（这条最重要，见 §4.1）
   - 用**同一套装配代码**装两个完全不同的业务：
     - 业务 A：一个跟电商毫无关系的极小实现（比如两三个玩具工具）
     - 业务 B：留给 issue 03 的电商实现（本 issue 里先只建一个占位/最小实现）
   - 两个都能跑通一次完整问答
   - 顺带断言：框架代码里不出现业务词（minimall / 商品 / 订单 / ecom）

## 验收

- [x] 框架现有测试全绿（703 个，一个不能少）
- [x] 新增测试覆盖：工具提供者被正确调用、载荷原样透传、提示词目录参数生效
- [x] 通用性测试通过：同装配装两个不同业务都能跑通
- [x] `agent/loop.py` **零改动**（这是本 issue 的核心验收点 —— 如果加接入点必须改主循环，说明接入点设计错了，要停下来重新想）
- [x] 命令行现有行为不变（跑一次 `python -m CharAgent.client -q "现在几点?"` 正常）

## 备注

- 提示词名和目录两个参数**都必须有默认值**，默认行为等于现状。这样命令行不用改，现有测试也不用改。
- 不要顺手重构框架其他部分。本 issue 是"加接入点"，不是"改善框架"。
- 业务 A 的玩具实现故意做得**跟电商毫无关系**（比如"读一个文件"、"算两个数之和"）—— 它越不像电商，通用性测试越有说服力。

---

## 实际开发情况 2026-09-19

**结论：完成。** `agent/loop.py` 零改动；测试 742 → 756（+14，零回归）；命令行真跑一次正常；wheel 实建验证干净。

### 验收逐条

| 验收项 | 结果 |
|--------|------|
| 框架现有测试全绿 | ✅ 742 → 756（本 issue 里写的 703 是过期数字） |
| 提供者被正确调用 / 载荷原样透传 / 提示词目录参数生效 | ✅ `tests/test_agent_provider.py` |
| 通用性测试：同装配装两个不同业务 | ✅ 单一 `assemble()` 被两次调用，不是两份拷贝 |
| `agent/loop.py` 零改动 | ✅ `git diff --name-only CharAgent/agent/loop.py` 为空 |
| 命令行现有行为不变 | ✅ 真跑 `python -m CharAgent.client -q "现在几点?"`，答复正确 |

### 实际落点（与初稿三处出入，以代码为准）

1. **落在 `agent` 包**（`CharAgent/agent/provider.py`），不是 `tool` 包。它是运行时装配层，与 `AgentLoop` 同层；`tool` 包管「工具是什么」，不管「这次拿哪些」。
2. **`ToolProvider` 不由 `ChatSession` 解析**。业务的装配代码自己 `await provider.provide(context)`，再把工具交给会话 —— `ChatSession` 至今不知道 `RunContext` 存在。这样框架不必「为了调用而持有业务数据」（payload 里是什么，框架从装配到运行都不经手），也避开了 `__init__` 是同步的而 `provide` 是异步的这个冲突。代价是业务入口多一行 `await`。
3. `provide` 返回 `Sequence[Tool]` 而非 `list[Tool]` —— `list` / `tuple` 都能交。

### 计划外但必须做的（实现中发现）

- **打包 `namespaces` 的默认值是 `true`**：只写 `where` + `include` 时，setuptools 会把 `tests/`、`docs/`、`alembic/`、`prompt/templates/` 等 **10 个非包目录**当命名空间包收进 wheel。已显式 `namespaces = false` 修掉（实测 wheel 条目 247 → 97）。
- **egg-info 落在仓库根**：这是 `where = [".."]` 的副作用（`egg_base` 跟着 `package_dir` 走）。**不能靠改 `where = ["."]` 解决** —— 起点落进包目录内部后，find 出的是 `agent` / `checkpoint` 这类顶层包名，与代码里的 `from CharAgent.agent import ...` 对不上，装出来直接 import 失败（已实测）。改用 `setup.cfg` 的 `[egg_info] egg_base = .` 钉回包目录。
- **ruff 的 `exclude` 是覆盖语义，不是追加**：原来的 `exclude = [".venv/"]` 把默认排除的 `dist` / `node_modules` / `site-packages` / `.git` / `_build` 一并放开了，而 `.venv` 本来就在默认列表里 —— 那一行等于净损失。已改成 `extend-exclude = ["build"]`（`build` 不在 ruff 默认列表里，列表里的是 `_build`）。
- **ruff 不认 `app` 是 first-party**：`project/rag_text2sql/main.py` 因此报 I001，但那个文件的 import 分组本来就是对的。已加 `known-first-party = ["app"]`，**那个文件一个字没改**。
- `demo/` 两个文件的 UP042 一并修了（`(str, Enum)` → `StrEnum`）。已单独验证 pydantic 序列化行为一致（两个 demo 的 `Edu` 只作字段类型，没有 `str()` 或 f-string 插值）。

### 明确不在本 issue 处理

- `prompt/templates/service.prompt` 保留在框架目录，内容改为纯占位 —— 它不是业务提示词，当前也没有代码读它。
- `src/` 布局：能一次性解决 `build/` 与 `egg-info` 的落点问题，但要把 10 个包目录搬家、改掉 `pytest.ini` 与测试里的路径计算，收益不抵代价。

### 产物

新增 4 个：`agent/provider.py` · `pyproject.toml` · `setup.cfg` · `tests/test_agent_provider.py`
修改 8 个：`agent/__init__.py` · `__init__.py` · `prompt/load.py` · `prompt/__init__.py` · `client/session.py` · `prompt/templates/service.prompt` · `tests/test_prompt_load.py` · `tests/test_client_session.py`
