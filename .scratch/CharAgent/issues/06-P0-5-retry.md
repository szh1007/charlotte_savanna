# 06-P0-5 — 重试 + 指数退避 + jitter + 幂等键

**What to build:** 重试策略：只对瞬态错误（429/5xx/超时）做指数退避 + jitter 重试，4xx 永久失败直接放弃；重试会重复计费 token（#13），需缓存响应或挂钩预算。幂等键（IdempotencyKey）生成与校验基础，为 P1 真实副作用防重复执行打底。

**Blocked by:** None — can start immediately

**Status:** done

- [x] RetryPolicy：指数退避 + jitter；瞬态错误重试、4xx 放弃（#13）
- [x] 重试上限与总耗时上限可控
- [x] 幂等键：生成 + 校验基础实现（防重复执行，P1 挂载到真实动作）
- [x] 边界测试：退避序列、jitter 随机性注入（#61 确定性）

## Comments

**2026-09-12 实施完成**（提交前 code-review 双轴审查 + 修复）。执行计划（用户审核通过）已随实施消费、草稿未入库 —— 计划要点已并入本 Comments 的 §2~§5 与 §8。落点：`CharAgent/retry/`（顶层包，对齐 DESIGN.md 目录结构 + issue 04/05 的包组织惯例；静态零件按惯例收进 `utils/`），门面 `__init__.py` 导出公共 API。

### 1. 模块落点与分工

| 文件 | 职责 |
|------|------|
| `retry/policy.py` | `RetryPolicy`（退避序列 / 三个上限裁决 / 三条注入缝）+ `is_retryable` 默认瞬态判据 |
| `retry/executor.py` | `retry_async` 通用驱动器：异常路径 + 响应不合格路径 |
| `retry/chat_model.py` | `RetryingChatModel`：ChatModel 协议的重试包装（SPI 组合，ADR-0001） |
| `retry/idempotency.py` | `IdempotencyKey`（生成 + 校验）+ `IdempotencyStore` 协议 + `InMemoryIdempotencyStore` |
| `retry/utils/` | `types.py`（RetryAttempt / ClaimResult / RetryCallback）· `errors.py`（RetryConfigError / IdempotencyKeyError） |

### 2. 退避与上限（ticket 第 1、2 条）

- 退避：`base = min(initial_delay * multiplier^(attempt-1), max_delay)`，再乘 `(1 - jitter * u)`，`u = random_source()` ∈ [0,1)。`jitter=0` 纯指数（确定性）；`jitter=1` 即 AWS full jitter（等待均匀落在 (0, base]）—— 抖动只用来打散惊群，**永不越过 base**
- 上限三个，任一命中即放弃：`max_attempts`（含首次）、`max_elapsed_seconds`（**预判**：本次等待已会撞破预算就不等 —— 边界「恰好等于仍可挤下」，与 `LoopGuard` 事后判定的「等于即触发」相反，已在 docstring 与用例写明）、`max_delay`（指数必须收敛）
- 瞬态判据 `is_retryable` 读异常自带的 `retryable` 标记（model 层异常族已按 429/5xx/连接/超时 = True、其余 4xx/配置/畸形 = False 标好）；未声明该属性的异常（工具错误 / 编程错误）一律不重试 —— 错试比漏试贵
- 三条注入缝 `sleep` / `time_source` / `random_source`（对齐 `LoopGuard.time_source` 惯例）：测试零真实等待且完全确定（#61）。`RetryPolicy` **无状态**（对比 LoopGuard 每 run 一个 `_started`），可跨 run / 并发安全复用

### 3. 重试落点：模型调用层，loop 与 model 零改动（决策 Q3）

`RetryingChatModel` 以组合方式包装 ChatModel（ADR-0001 的 SPI 用法），逐参数透传（temperature / top_p / seed / max_tokens / thinking / reasoning_effort / stream，#68 每次尝试一致）。故 `AgentLoop` 与 `model/` 一行未改，既有 281 个用例原样通过。接线留给 issue 10 的 CLI：`AgentLoop(model=RetryingChatModel(chat_model_from_env()), ...)`。

### 4. 两条失败通道与 #13 双计费（决策 Q1）

| 通道 | 判据 | 耗尽收场 |
|------|------|---------|
| 异常路径 | `is_retryable(exc)`（429 / 5xx / 连接失败 / 超时） | 抛**最后一个**异常（原对象、不包装、traceback 保真） |
| 响应不合格路径 | `finish_reason=insufficient_system_resource`（官方指引「资源不足稍后重试」） | 返回**最后一个**结果（不伪造异常）→ loop 照旧判 `SERVER_INTERRUPTED`，03-api.md §2.2 终局契约不变 |

- **双计费不给假账**：异常路径拿不到 usage；响应路径那次响应**已经计费**。被丢弃的用量随 `on_retry` 的 `RetryAttempt.result`（原样 ModelResponse，可读 usage）交回调用方，P1-11 据此记账；P0 **不**并入 `LoopGuard` 的 token 预算（避免假账，裁决属 P1-11）。开关 `retry_upstream_interrupted=False` 可关（P1-11 预算 / P2 语义缓存要「不再烧一次」时用）
- **`aborted` 故意不重试**：语义含糊（可能是用户侧主动中断），重试可能违背用户意图 —— 原样返回给 loop 如实上报
- **`CancelledError` 不重试不吞**：属 `BaseException`，`except Exception` 天然不拦，kill switch 优先于重试（#3，用例钉住）
- `on_retry` 在等待**之前**回调（调用方立刻拿到等待秒数），异常**向上传播**（对齐 `event_sink`：调用方自己的回调，通知失败要让它看见；与 hook 的插件异常隔离语义不同）

### 5. 幂等键与存储（ticket 第 3 条，决策 Q2）

- `IdempotencyKey`：`generate()`（uuid4 hex 32 字符）+ 构造即校验（长度 1~255、字符集 `[A-Za-z0-9._:-]`、首字符为字母或数字）—— 客户端传入的键会变成下游存储 key / PG 主键 / 日志字段，通配、空白、控制字符必须挡在入口；拒绝消息带**实际长度**与白名单，且超长键只做截断预览（日志安全）
- `IdempotencyStore`（协议）+ `InMemoryIdempotencyStore`：claim（CLAIMED 首次可执行 / IN_PROGRESS 有在途 / COMPLETED 带既有结果）、complete、release。**release 只放行在途记录**（已完成结果不被后到的失败抹掉）；单个事件循环内 dict 读改写无 await，进程内天然原子
- **组合用例证明 #13 的核心**：动作已完成但响应丢失 → 重试时 claim 命中 COMPLETED，直接取既有结果，副作用恰好一次（这正是「重试」与「幂等键」必须同条交付的原因）

### 6. 验收证据

- 新增 **56 个用例**（4 个测试文件）：`test_retry_policy.py`（退避序列精确值 / jitter 注入与「不消费随机源」/ 上限裁决三例 / 配置校验参数化 6 例 / 瞬态判据）· `test_retry_executor.py`（成功零开销 / 瞬态重试与记录单逐字段 / 永久错误立即抛 / 耗尽抛最后一个 / 耗时预算提前放弃 / 响应路径重试与耗尽返回最后结果 / CancelledError / 回调时序与异常传播）· `test_retry_chat_model.py`（respx 拦真实适配器：429→200 与请求体逐参数透传、400 立即抛、5xx 耗尽抛最后一个、连接超时、连接失败、上游中断重试带 usage、aborted 不重试、开关关闭、参数透传轨迹、aclose 委托、**与 AgentLoop 组合的两条端到端**：429 后仍以 final 收尾 / 耗尽则上抛且不发终局事件）· `test_retry_idempotency.py`（生成与校验含参数化 9 例、拒绝消息长度、存储四态、组合语义）
- 全量 **337 passed / 11 deselected**（integration marker 默认排除；基线 281 → 新增 56）· Ruff check + format 零告警
- 未跑真实端点（`-m integration`）：本轮**不改** model / loop 主体，`RetryingChatModel` 的接缝已由 respx 拦真适配器的用例覆盖（与 issue 05「因改了 loop 主体才必跑」的判据一致）
- 测试替身收口：`_FakeClock` 出现第 3 处 → 按项目 DRY ≥3 阈值抽到 `tests/doubles.py`（`FakeClock` / `RecordingSleep` / `FixedRandom`），`test_loop_guard.py` 与 `test_loop_events.py` 的本地副本已改为 import；`tests/helpers.py` 保持「wire 样本与常量」定位
- 文档同步：`01-architecture.md`（§2 图加 Retry 节点并改为 Loop → Retry → Model；§3 新增「重试不在 loop 里」）· `03-api.md`（§1 `request_id` 校验规则 + `IdempotencyStore` 三态；§4 补「本表错误码是重试耗尽后的结果」与双计费归属）· `05-roadmap.md` P0-5 落到 `retry/` 文件级 · `difficulties/02-stability.md` #13 三条实现要点 · `CONTEXT.md`（`RetryPolicy` / `IdempotencyKey` 定稿，新增 `RetryAttempt` / `RetryingChatModel` / `IdempotencyStore`，含 `_Avoid_`）· `DESIGN.md` 目录行文件级；根 `CLAUDE.md` / `README.md` 未动（CharAgent 尚未登记在根模块表）

### 7. code-review 双轴审查修复（2026-09-12）

- **术语撞词（硬违规）**：`RetryAttempt` 原被称「重试记录」，正是 `CONTEXT.md` 该词条的 `_Avoid_`（易与 `RunState` 的 retrying 混淆）→ 统一改「失败尝试记录 / 记录单」；`RetryingChatModel` 不再叫「适配器」（该词在仓库内专属 `model/client_httpx.py` / `client_sdk.py` 两个传输适配器），统一「重试包装」
- **测试规范**：`test_policy_rejects_invalid_bounds` 由 6 组 `pytest.raises` 挤在一个用例 → 参数化（一例一行为）；幂等组合用例的 Act 阶段 `assert isinstance` 去掉（`action() -> object` 直接返回既有结果）
- **轨迹断言失真风险**：`_FakeModel` 原记录 `messages` 活引用（`calls[0] == calls[1]` 会因同一 list 对象而恒真）→ 改浅拷贝冻结（与 `mock_llm.ScriptedModel` 同一防坑）
- **spec 对表**：计划 §3.5 要求拒绝消息含「实际长度」—— 已补（并让 `_preview` 去掉重复的字符数）；计划 §5 的「连接失败」用例缺失 → 补 `httpx.ConnectError` 重试用例；计划 §6 的 `IdempotencyStore` 独立词条 → 已补；计划 §8 的「无熔断」边界 → 写进 `policy.py` 模块 docstring（连续 429 靠三条上限兜底，熔断 + failover 属 P1-3）
- **`utils/__init__.py` 自述与实现不符**：补一句「域内常量跟随其所属类型（如幂等键的长度与字符集在 `idempotency.py`），不上浮到这里」

### 8. 未采纳（附理由）

- **复用 `ScriptedModel` 代替 `_FakeModel`**：重复 2 次未达项目 DRY ≥3 阈值，且 `_FakeModel` 有 `ScriptedModel` 没有的 `closed` 标志（aclose 委托断言必需）；扩展 `mock_llm.py`（issue 04/09 的共享文件）收益小于改动面
- **把 `SERVER_INTERRUPTED` frozenset 上移到 `model/utils/types.py` 复用**：retry 的判据**故意更窄**（只认 `insufficient_system_resource`，排除 `aborted`，理由见 §4），复用会引入新的分叉判断；同时避免为一个常量搬动 agent 层既有代码
- **把响应判据做成 `ModelResponse` 的属性（Feature Envy 建议）**：该判据含开关（`retry_upstream_interrupted`）与原因文案（「已计费 token N」），是重试策略而非模型数据的纯查询；且 `model/` 属 issue 01/02 已验收面，不动
- **`IdempotencyStore` 协议 + InMemory 实现被判「Speculative Generality」**：这是 ticket 第 3 条与计划 Q2 的**明文交付**（「为 P1 真实副作用防重复执行打底」），非自造抽象
- **默认值散文（3 次 / 0.5s / 60s 在多处 docstring 重复）**：面向使用方的 API docstring 应自带默认值（不必翻 policy.py），属有意的可读性取舍

### 9. 遗留（不属本 issue）

语义缓存（P2-4 cost 插件）· token 计量与预算硬上限（P1-11）· 熔断 + failover 与分层超时（P1-3）· Redis/PG 幂等存储 + TTL + Saga 补偿 + 分布式锁（P1-4）· 根 `CharAgent/__init__.py` 门面仍未导出 agent/stream/hooks/retry 公共 API（既有不一致，与 P0-3/P0-4 同批处理）

**接线状态（2026-09-12 复核）**: 本 issue 交付的 `retry/` **目前生产调用点为零** —— `AgentLoop` 在非测试代码里没有任何构造点（`grep "AgentLoop("` 只命中 docstring），`RetryingChatModel` 只被测试构造。这是计划 Q3 的**有意结果**，不是漏接：重试以组合方式挂在 **ChatModel 协议层**（`ChatModel` 是 `Protocol`，不是基类 —— 不要求继承，只看形状），接线发生在**构造 loop 的那一行**：

```python
loop = AgentLoop(model=RetryingChatModel(chat_model_from_env(), policy=RetryPolicy(...)), ...)
```

该行属 issue 10（CLI 入口，`Blocked by: 04, 05, 07, 09`；`python -m CharAgent.cli` 尚不存在），已作为待办写进 [10-P0-acceptance-cli-demo.md](10-P0-acceptance-cli-demo.md)。复核结论：维持方案 A（loop 不加 `retry_policy=` 参数、不默认开启重试）—— 理由：默认开启会让每轮 `elapsed_ms` 含重试睡眠（可能提前触发 `TIME_LIMIT`），且与 loop 现有契约注释「模型调用失败直接抛出、本层不包装不吞」冲突；P1 server 要按 run/租户调策略时，入口一行也比 loop 参数更灵活。接线后的行为已由 `CharAgent/tests/test_retry_chat_model.py` 的两个 loop 组合用例覆盖（429 后仍以 final 收尾 / 耗尽则上抛且不发终局事件）。

### 10. 交付后 API 精化（2026-09-13）：on_retry 收序列

- 缘起：用户提问「想挂多个回调怎么办」→ 三个方案（① 调用方自写组合函数 ② `on_retry` 改收 `Sequence[RetryCallback]` ③ 引入 `RetryHookRegistry`）中选定 **②**：框架内建「多观测者」，调用方不必自己写组合器，也不为暂无的隔离 / 动态注册需求提前造注册表
- 变更：`retry_async(on_retry: Sequence[RetryCallback] | None)` 与 `RetryingChatModel(..., on_retry=...)` 同步改签名；`_notify_retry` 由「调单个回调」改为「按序列顺序串行调用」（同步回调直接调、异步回调 await 到位）
- 语义三条（已写进 docstring）：① **顺序 = 序列顺序**，串行 `await`（在重试关键路径上：notify 之后才 sleep，慢回调会拖慢重试）；② **不做异常隔离** —— 某个回调抛异常即中止其后的回调并把异常向上抛（与 hooks 注册表的插件隔离语义相反：观测链断了要让它看见，静默跳过会让账目悄悄少记一笔）；③ 空序列 / `None` = 不挂观测（零开销）
- 兼容性：单回调写法变为 `on_retry=[callback]`；`retry/` 尚无生产调用点，无迁移成本
- **迁移护栏（复核时补，并更正上文）**：实测发现旧写法（裸函数）**只在真的发生重试时**才炸 `TypeError: 'function' object is not iterable` —— 首轮成功或 `max_attempts=1` 的运行根本走不到通知处，传错的参数会被**静默吞掉**。故在 `retry_async` 入口加一行 `callable(on_retry)` 检查：命中即抛 `RetryConfigError`（消息含正确写法 `[callback]`）。**更正**：本文档先前写「传裸函数会立刻 TypeError」不准确，实际是加护栏之后才「立刻」
- 证据：新增 4 用例（多回调按序调用含同步/异步混用 · 中途异常跳过其后的回调 · 空序列零开销 · 裸函数入口护栏）；全量 **341 passed / 11 deselected**（337 → 341），Ruff check + format 零告警
- 文档同步：`retry/executor.py`（Args 条目 + `_notify_retry` docstring）· `retry/chat_model.py`（Args 条目）· `retry/utils/types.py`（`RetryCallback` 注释）· `docs/CONTEXT.md`（`RetryAttempt` 词条）
