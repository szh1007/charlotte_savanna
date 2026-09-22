# 16 · 框架侧：上下文压缩（账本 / 视图分离）

**Status:** done

**Type:** task

**Blocked by:** 无（可与 15、17 并行）

**上游:** `../PRD.md` §4.9（L2.5 行）、`CharApp/docs/PLAN.md` §5（L2.5）、`CharAgent/docs/DESIGN.md` 难点表 **#7 上下文压缩**

## 做什么

**一个会话聊得越久，每一轮都要把全量历史重发一遍。** `conversation_id` 在标签页里是固定的（`sessionStorage`），所以历史只增不减：成本与首字延迟随轮数线性上涨，直到撞上模型窗口。框架对口的难点是 **#7「上下文压缩 —— 摘要 + 截断，但绝不能破坏 `tool_calls` 的结构」**，它在难点表里标着 **P1**，至今一行没写。

本片给框架补上这条能力，形状是**账本 / 视图分离**（用户 2026-09-22 选定）：

```
账本（快照）           append-only，一直长，一字不改 —— 断点续跑与回溯靠它
   ↓ 投影（每次模型调用前算一次，不落地）
视图（送给模型的）     系统提示 + 压缩摘要 + 最近 N 轮完整对话 —— 越聊越短
```

**业务侧配参数、框架提供能力**（PRD 已有的分工）：`AgentLoop` 多一个 `compactor=` 参数，默认 `None` = **与今天逐字一样**。

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| **送模型只有一个调用点**：`self._model.generate(state.history, tool_specs, ...)`，在 `_decide` 内 | `CharAgent/agent/loop.py:530-539`（`_decide` 定义 `:503`） |
| 传给 `generate` 的第一个位置参数**就是 `state.history` 本体**（同一个列表引用，不是拷贝） | `loop.py:531` |
| `state.history` 全仓**只有 9 处 append**，全在 `loop.py`（工具轮 2 处 / 截断 4 处 / 中断 1 处 / 完成 1 处 / 补做挂起 1 处） | `loop.py:581` `:593` `:621` `:627` `:634` `:635` `:649` `:659` `:789` |
| `_save_checkpoint()` 从 `state.history` 浅拷贝落盘，`parent_id` 取 `state.last_checkpoint_id` | `loop.py:843-858` |
| `LoopState` 定义在 `agent/utils/types.py`，12 个字段 | `agent/utils/types.py:63-111` |
| `LoopResult` 字段：`messages` / `content` / `finish_reason` / `outcome` / `turns` / `turn_count` / `truncation_count` / `total_tokens` / `elapsed_ms` | `agent/utils/types.py:153-161` |
| `CheckpointState` 现在**只有 6 个字段**（`messages` / `turn_count` / `total_tokens` / `truncation_count` / `content_parts` / `suspension`），docstring 明说「没有 content / finish_reason / outcome」 | `checkpoint/utils/types.py:161-166` |
| **有一条用例断言 `CheckpointState.__slots__` 恰好是这 6 个** —— 它是「续跑输入契约」的守卫，加字段**必须**连带改 | `tests/test_checkpoint_serialization.py:505-516` |
| `SCHEMA_VERSION = 3`，逐版本改动写在 docstring | `checkpoint/utils/types.py:41-51` |
| 迁移表 `MIGRATIONS: dict[int, Callable]`，键是「从哪个版本出发」，注册在文件末尾两行 | `checkpoint/utils/migrations.py:53`、`:141-142` |
| 迁移只处理记录主体 `{"state", "metadata"}`；`from_version > SCHEMA_VERSION` 直接拒读 | `migrations.py:15-18`、`:70-74` |
| **未知 / 多余字段一律忽略**（向前兼容的既有约定），缺失字段取默认值 | `checkpoint/utils/fields.py:12-13`、`:23-75` |
| `_body_payload` **逐字段**列出落盘形状 —— 新字段不加在这里，**写了也存不进去** | `checkpoint/serialization.py:396-420` |
| `fixtures/checkpoint_v3.json` 与序列化产物**逐字比对** | `tests/test_checkpoint_serialization.py:524-536` |
| 落盘形状快照（两帧）会因为新增字段而变 | `tests/fixtures/snapshots/checkpoint_frames_tool_path.json`、`tests/test_snapshots.py:199-217` |
| 重生成开关 `UPDATE_SNAPSHOTS=1`；快照不存在时会写文件并**判红**（不静默通过） | `tests/snapshots.py:137-143`、`:70-76` |
| `EventType` 现在六个值；docstring 已注明 HITL 会再追加 `approval_required`（由 loop 之外的模块产出） | `stream/utils/types.py:33-38`、`:29-31` |
| 事件唯一出口 `EventBus.emit(type, **data)`，内部先走 `_advance()` 的状态机校验 | `stream/bus.py:116` |
| 载荷构造是 `events.py` 里的纯函数，调用点全在 `loop.py` | `agent/utils/events.py:64-94`、`loop.py:576-599` |
| `count_tokens(usage)` 取的是 `usage.total_tokens`（**含输出**），不是「请求有多大」 | `agent/utils/messages.py:21-30` |
| `tool_wire` 的工具返回正文**没有长度上限**（只有发往浏览器的**事件流**截到 200 字符，全文仍在 wire 历史里） | `agent/utils/messages.py:63-70`、`stream/utils/types.py:47` |
| `AgentLoop.__init__` 的校验全写在函数体内（**没有** `__post_init__`）；keyword-only 参数从 `guard=` 起 | `loop.py:199-231` |
| `LoopGuard` 明确说自己是**运行级**刹车，且「调用前先预估 token 不属本模块」 | `agent/guard.py:17-33` |
| 业务侧现在把三个 guard 值从 `MinimallService` 传进 `ChatSession`（`service.py:266-270`）—— 新参数照这个形状走 | `CharApp/minimall/service.py:224-226`、`:266-270` |
| `MockLLM.scripted` 的脚本元素可以是**异步工厂** `Callable[[list[ModelMessage]], Awaitable[ModelResponse]]` —— 断言「当轮实际送了什么」就靠它 | `tests/mock_llm.py:55`、`:560-568` |
| 两个快照 fixture 由**真实样本回放**驱动（`MockLLM.replay("tool_path")`） | `tests/record_llm_samples.py:82-104`、`tests/fixtures/llm/tool_path.json` |
| `hooks/registry.py` 的 docstring 原文：「**载荷是活引用**: before_turn 拿到的 messages 就是 loop 的历史列表本体, memory 插件即在此挂载 (注入记忆); 想只读就自己拷贝」 | `CharAgent/hooks/registry.py:56-58` |

## 具体任务

### 1. 能力与参数的形状（放 `agent/` 包，与 `ToolProvider` 同层）

- **`TokenCounter` 协议**：`def count(messages: Sequence[ModelMessage]) -> int`。默认实现 = **锚式估算**：以最近一次响应的 **`usage.input_tokens`**（请求有多大）为锚，加上之后新增消息的字符启发式增量。**权威值只有 API 给的 usage**，估算只用来判阈值 —— 这条要写进 docstring
  - **不引 `tiktoken`**：它算不准中文，多一个依赖只换来假精度。业务可注入自己的实现
  - 注意别直接复用 `count_tokens()`：它取的是 `total_tokens`（含输出）
- **`CompactionPolicy` 协议**：`def apply(history, *, summary, counter) -> CompiledView`，返回「送给模型的消息列表 + 新的摘要 + 摘要覆盖到第几条 + 这次做了什么（裁了几条 / 截了几条 / 有没有摘要）」
- **默认实现**（名字待定，建议 `TrimAndSummarize`）：三件套 + 一条硬不变量
  1. **窗口裁剪**：保 system（第一条）与最近 N 轮，**按整轮**为单位
  2. **工具结果截断**：被裁掉那一段里的 `tool` 消息正文截到阈值（最新一轮不截）
  3. **滚动摘要**：把「上一条摘要 + 本次新裁掉的段」一起交给 summarizer 重压（**不是**每次只压新掉的那段 —— 那样更早的信息会被逐次稀释到消失）
- **硬不变量（写进 docstring 并逐条有用例）**：
  - 绝不拆散 `assistant(tool_calls)` 与其 `tool` 消息（裁剪单位是**轮**，不是条）
  - system 第一条永不裁
  - 至少保留最近 1 轮完整
  - 裁完的序列必须仍是合法的 wire 序列（`tool` 消息紧跟带 `tool_calls` 的 `assistant`）
- **触发与水位线**：估算 ≥ 阈值 → 压；压到 `阈值 × 水位线比例` 以下就停手（避免每轮都触发的抖动）
- **摘要那一次模型调用**：注入 `summarizer: ChatModel`（默认用主模型），`max_tokens` 单独给一个小的；**失败降级为纯裁剪**（不因为摘要失败让用户这一句问不出来）+ 发一条 warning 事件；它的 token **计入本次运行预算**（确实花了钱）但**不计入 `max_turns`**（它不是一次模型决策）

### 2. 接缝（`AgentLoop` + `ChatSession`）

- `AgentLoop.__init__` 加 `compactor: CompactionPolicy | None = None`；校验照既有形状写在函数体内
- **改动只落在 `_decide` 一处**：算出 `view = compactor.apply(...)`，把 **`view.messages`（副本）** 传给 `generate`，**`state.history` 一个字不动**（它仍是 append-only 的账本，`_save_checkpoint` 因此**零改动**）
- `ChatSession.__init__` 加 `compactor=` 并透传给 `AgentLoop`（业务只碰 `ChatSession`，不碰 loop）

### 3. 摘要的持久化（`SCHEMA_VERSION` 3 → 4）

- `LoopState` 加两个字段：`summary: str | None`、`summary_covers: int`（摘要覆盖到 `history` 的第几条）
- `CheckpointState` 加同名字段（**这就是那条 6 字段断言用例会红的原因**，连带改它）
- `_save_checkpoint` 带上这两个值；`resume` 路径要把它们**回灌**进 `LoopState`（`_run` 现在只接 `messages=`，要一起传）
- `_body_payload` 加这两个字段（不加就存不进去）；新增 `_v3_to_v4` 迁移函数并 `MIGRATIONS[3] = _v3_to_v4`
- **老快照必须还能读回来**：`fixtures/checkpoint_v3.json` 原样读回（缺的字段取默认 = 「那时候没有摘要」），新增 `checkpoint_v4.json` 与落盘产物比对

### 4. 第七类事件（让压缩看得见）

- `EventType` 加 `CONTEXT_COMPACTED = "context_compacted"`；`_advance()` 的白名单跟着加，并确认它**不能在终局之后发**
- 载荷：`{裁掉几条 / 截断几条 / 压后估算 token / 省下多少 / 走没走摘要 / turn}`。构造放 `events.py` 的纯函数，调用点在 `loop.py`
- 业务侧的 `redaction.py` 只碰 `tool_call` / `tool_result`，这条新事件会**原样**透到浏览器（正合需要）；BFF 的字节透传与 `TERMINAL_EVENTS` 都不受影响

### 5. 测试

- 用 `MockLLM.scripted` 的**异步工厂**断言「第 N 轮实际送进 `generate` 的 messages 是什么」—— 这是本片最核心的一条接缝
- 不变量逐条钉：整轮裁剪（含工具结果条数与 assistant 不一致的边界）· system 不裁 · 至少留 1 轮 · 裁完仍是合法序列
- 触发与水位线：不到阈值不动、超阈值压到水位线下（连压两次的用例）
- 摘要：滚动（第二次压缩的输入含上一次摘要）· 失败降级为纯裁剪 + warning
- 快照仍全量：压缩前后**帧数只增、旧帧内容不变**；老快照读回；未来版本拒读
- `pytest CharAgent` 全绿（含 `-m pg` / `-m pg_db` 两个默认排除的标记）

## 验收

- [x] **不配 `compactor=` 时行为与今天逐字一样** —— 一条既有用例都没改，且两个快照 fixture（事件序列 / 落盘形状）原样通过
- [x] 有一条用例断言**第 N 轮实际送模型的 messages**（靠 `MockLLM` 的异步工厂，不是靠看返回值）
- [x] 裁剪按**整轮**：绝不出现在「`assistant(tool_calls)` 留下了、它的 `tool` 消息被裁掉」的序列（含工具结果多于/少于调用数的边界用例）
- [x] system 第一条永不裁；最近 1 轮完整保留
- [x] 工具结果截断生效：老 `tool` 消息被截到阈值，**最新一轮不截**
- [x] 未超阈值时**不压**；超阈值压到水位线以下；不会每轮都压（连压两次的用例）
- [x] 滚动摘要：第二次压缩的输入含上一次摘要（用例断言送进 summarizer 的文本）
- [x] 摘要失败 → 降级为纯裁剪，用户这一句照样答得出来，且发了 warning 事件
- [x] 摘要的 token 计入 `total_tokens`，但**不**增加 `turn_count`
- [x] `SCHEMA_VERSION == 4`；`checkpoint_v3.json` 读回后 `summary is None`；新增 `checkpoint_v4.json` 与落盘逐字比对；未来版本仍被拒读
- [x] `context_compacted` 事件只在**真的压了**的时候发；终局之后不发
- [x] `pytest CharAgent` 全绿；`ruff check` / `ruff format --check` 干净
- [x] `UPDATE_SNAPSHOTS=1` 重生成的那两个 fixture 是**有意**变更，理由写进本 ticket

## 备注

- **为什么不挂在 `BEFORE_TURN` 钩子上**（它拿到的 `messages` 就是活引用，原地改真的能改）：① 那是 **fire 类**点，契约原话是「忽略, 没人看」——把「改变下一步输入」的逻辑塞进观察点，等于让 hook 契约变成"某些点其实可以改载荷"，正是 issue 08 拒绝给 fire 加返回值时想避免的事；② 压缩需要**框架保证的不变量**（配对、system 不裁、至少留 1 轮），这是通用知识，不该每个业务各写一遍；③ 摘要是一次真实模型调用，它的 usage 要计入运行预算，钩子点拿不到那个回写口。**如实记**：钩子那条路能跑，只是接缝选错了 —— registry 的注释是为「**注入**记忆」（加消息）写的，压缩是「**重写**」（删 / 替换），两件事
- **压掉的信息没有丢**：旧帧还在快照里（`list_history` 可回溯）。真正的「长期记忆 + 按需检索」是另一个组件（难点 #4 的情景记忆），**本片不做**，但要在文档里写清这条边界 —— 否则面试时会问「压掉的就永远没了？」
- **与 `LoopGuard` 的分工**：guard 是**运行级刹车**（这次运行总共别烧太多 → 直接停），压缩是**单请求治理**（别让这一次请求变大 → 压完继续）。两个数各自算，都要留
- 阈值 / 保留轮数 / 工具结果上限的**具体数值由业务侧定**（见 issue 18 的 `CHARAPP_CONTEXT_*`）；框架只提供能力与默认关闭

## 实际开发情况 2026-09-22

**一句话**：能力与接缝全部落地 —— 账本 / 视图分离（`AgentLoop` 多一个 `compactor=`，透传到 `ChatSession`）、估算器（锚式）+ 默认策略（窗口裁剪 / 工具结果截断 / 滚动摘要）、快照 `SCHEMA_VERSION` 3 → 4（含 `_v3_to_v4` 与新 fixture）、第七类事件 `context_compacted`；**不配 `compactor` 时行为逐字不变**（既有用例零改动，事件序列快照原样通过）。测试：框架 **892 passed / 65 deselected**（比本片开工时 +42：压缩 35 · 事件 2 · 终端渲染 2 · 会话装配 2 · 快照序列化 1）· 业务 **186 passed** · 标记集 **51 passed / 3 skipped**（`-m "pg or redis or pg_db"`，3 条 skip 是本机没配那两样服务的既有行为）；`ruff check` / `ruff format --check` 干净。本片**没碰** `CharApp/` 与 `app/minimall/` 一行代码 —— 业务侧配参数是 issue 18 的事。

### 一、拍板的开放项（ticket 没定 / ticket 与代码对不上，实现时定下来的）

| 项 | ticket 说的 | 落地的 | 为什么 |
|----|------------|--------|--------|
| `TokenCounter` 几个方法 | 「`def count(messages) -> int`」一个 | **两个**：`count` + `note_usage(usage, *, message_count)` | 锚必须有个回灌口（上游 usage 到了要更新它）。框架的既有约定是**结构性协议、不用 `runtime_checkable`**（见 `agent/provider.py`），于是没有 `isinstance` 可用来「有就调」—— 要么把口子明写在协议上，要么在 loop 里 `getattr` 嗅探（更糟）。代价：真分词器要多写一个空实现（docstring 里写明了） |
| `apply` 的同步 / 参数 | 「`def apply(history, *, summary, counter)`」 | **`async def apply(history, *, summary, summary_covers, counter, summarizer=None)`** | ① 摘要是一次**真实模型调用**，同步方法写不出来；② 多一个 `summary_covers`（滚动摘要要算「这次新裁掉哪一段」）；③ 多一个 `summarizer` —— 它是「默认用主模型」的落点（loop 每次把 `self._model` 传进去，策略自己注入的那个优先），否则「默认主模型」只能由业务在装配时手动填 |
| 「整轮」是哪一轮 | 「保 system 与最近 N 轮，**按整轮**为单位」 | 切点只落在**提问**（user 消息）处：一轮 = 一个提问 + 为它做的全部决策 | 若按「一次模型决策」切，会出现「assistant 的答案留下、它回答的那个提问被裁掉」—— 保留段不成一段对话（摘要失败时更明显）。按提问切之后，保留段与被裁段各自都是完整对话，而配对约束（`assistant(tool_calls)` 与它的 `tool` 同进退）照样满足 |
| 「发一条 warning 事件」 | §4「摘要失败 → 降级 + 发一条 warning 事件」 | **不新开事件类型**：降级原因挂在 `context_compacted` 的 `warning` 字段上（正常时是 `None`，字段恒在） | 事件类型是**给前端渲染的契约**：多一类就逼前端多写一个分支（而 CharApp 前端对认不出的类型是直接忽略，等于静默）。「走没走摘要」本来就已经在 `summarized` 字段里，`warning` 补的是**为什么没走**。另外框架侧同时 `logger.warning` 留痕（不静默吞） |
| `AgentLoop` 加几个参数 | §2「加 `compactor=`」 | **`compactor=` + `counter=`**（只给 counter 不给 compactor 报 `LoopConfigError`） | §1 自己写了「业务可注入自己的实现（真分词器）」，那就得有个注入口；只给一半属配置写错，装配期报错（与 `saver` / `thread_id` 成对给同一条纪律） |
| 摘要跨不跨 run | §3 只写了「`resume` 路径要回灌」 | **跨 run 也不断链**：`LoopResult` 多出 `summary` / `summary_covers`，`run()` 多两个入口参数，`ChatSession` 自己收着、下一句问话递回去 | 会话真正的用法是「一个 `ChatSession` 横跨很多次 `ask()`」，每次 `ask()` 都是一次新 run；只做 resume 回灌的话，第二句问话就是个全新进度 → 同一段旧历史被重压一遍（内容不会错，但白花一次摘要调用，滚动摘要的收益也丢一半）。这是本片唯一一处公共 API 增长，理由记在这里 |
| 「压后估算」与「省下多少」 | §4 要求两个数 | 两个数**不是同一把尺子** | `estimated_tokens` 是「这份视图作为一次请求大概多大」（锚在上游给的 `input_tokens`，含工具 schema 这类固定开销）；`saved_tokens` 是账本与视图用**同一个字符启发式**量的差。凑成同一口径就得放弃锚（那是「这次请求真实多大」的唯一近似），所以**不凑**，改为在 `CompiledView` docstring 与 `context_compacted_data` 里写明「别相加」 |

### 二、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `CharAgent/agent/compaction.py` | **新增**：`CompiledView`（投影产物 + 这次做了什么）、`TokenCounter` 协议 + `AnchorTokenCounter`（锚式估算）、`CompactionPolicy` 协议 + `TrimAndSummarize`（三件套：窗口裁剪 / 老工具结果截断 / 滚动摘要；四条硬不变量；触发与水位线；摘要失败降级）、切点与视图两个纯函数 |
| `CharAgent/agent/loop.py` | `__init__` 加 `compactor=` / `counter=`（含配对校验）；**唯一改动点 `_decide`**：新增 `_compile_view()` 投影出视图再 `generate`，账本一字不动；每轮把 `response.usage` 回灌给估算器；`run()` 多 `summary` / `summary_covers` 两个入口；`_seed_from_checkpoint` 回灌压缩进度；`_save_checkpoint` 落这两个字段；模块 docstring 加一条「上下文压缩线」 |
| `CharAgent/agent/utils/messages.py` | `estimate_tokens`（汉字一字一 token、其余四字符一 token + 每条固定开销）、`truncate_text`、`message_text`（摘要材料渲染）、`summary_request` / `summary_view_message` + 摘要指令与视图前缀文案 |
| `CharAgent/agent/utils/types.py` | `LoopState` +`summary` / `summary_covers`（进度，跨轮累积）；`LoopResult` +同名两个字段（出口，供跨 run 续接） |
| `CharAgent/agent/utils/events.py` | `context_compacted_data()` 载荷构造（纯搬运，不重算） |
| `CharAgent/agent/utils/errors.py` | `CompactionConfigError` |
| `CharAgent/agent/__init__.py` · `CharAgent/__init__.py` | 门面导出五个新名字 + 错误（根门面的 `__all__` 有防漂移用例守着） |
| `CharAgent/stream/utils/types.py` | `EventType.CONTEXT_COMPACTED`（第七类；旁路类，受「终局之后不得再发」约束） |
| `CharAgent/stream/bus.py` | `_advance` 的注释与模块 docstring：说明压缩事件与工具配对无关、同样受终局约束（行为没改，它本来就落在「无状态约束」那支） |
| `CharAgent/checkpoint/utils/types.py` | `SCHEMA_VERSION` 3 → 4（版本历史补一行）；`CheckpointState` +`summary` / `summary_covers` |
| `CharAgent/checkpoint/utils/migrations.py` | `_v3_to_v4`（纯加字段）+ 注册 `MIGRATIONS[3]`；版本历史补 v4 |
| `CharAgent/checkpoint/serialization.py` | `_body_payload` / `_build_state` 两处加上新字段（不加就存不进去） |
| `CharAgent/client/session.py` | `ChatSession` 加 `compactor=` / `counter=` 透传；**自己收着压缩进度**（跨 `ask()` 递回）；`_reclaim_progress` 连摘要一起收（与历史同源） |
| `CharAgent/client/render.py` | 第七类事件的终端版式 `_line_context_compacted`（压了多少 / 省了多少 / 走没走摘要 / 降级原因）+ 标签与颜色 |
| `CharAgent/client/app.py` · `client/__init__.py` · `stream/utils/__init__.py` · `server/utils/types.py` | 「六类事件」这类**事实性说法**跟着改（共 4 处） |
| `CharAgent/docs/DESIGN.md` | ① 的硬骨头补一条 **#7 压缩是「投影」不是「删历史」**（含三件套 / 锚 / 水位线 / 降级边界）；落地行补 `agent/compaction.py`；#4 行的计数更新 |
| `CharAgent/docs/difficulties/01-core-loop.md` | #4 的「六类（已实现）」补成七类 + 日期（照本仓「日期补记」的先例） |
| `CharAgent/tests/test_loop_compaction.py` | **新增 35 条**：估算 4 · 策略 13 · 接缝 9 · 畸形形状与帧边界 4 · 配置 1 · 其余为预算 / 事件 / 快照 |
| `CharAgent/tests/test_stream.py` | +2：压缩事件是旁路、终局之后被拒；类型清单与大白话版改七类 |
| `CharAgent/tests/test_client_session.py` | +2：会话把策略转交出去（事件透出）、摘要在两句问话之间不断链 |
| `CharAgent/tests/test_client_render.py` | +2：压缩那一行（正常 / 降级） |
| `CharAgent/tests/test_checkpoint_serialization.py` | `test_v3_progress_keeps_only_resume_inputs` → **`test_v4_...`**（8 个字段）；新增「v3 快照读回后 `summary is None`」「v4 与 fixture 逐字比对」；v2 迁移那条的断言补上新字段（顺流升到 v4 的加法） |
| `CharAgent/tests/fixtures/checkpoint_v4.json` | **新增**（非空 `summary` + `summary_covers`，与序列化产物逐字比对）；`checkpoint_v3.json` **留着不删** —— 它是「老快照读得回来」的样本 |
| `CharAgent/tests/fixtures/snapshots/checkpoint_frames_tool_path.json` | `UPDATE_SNAPSHOTS=1` 重生成：**`schema_version` 3 → 4，每帧 state 多 `summary: null` / `summary_covers: 0` 两行**。这是本片**唯一**重生成的快照；事件序列快照（`event_stream_tool_path.json`）**原样通过** —— 回放场景没配 compactor，正是「默认关闭」的证据 |

### 三、验收逐条

| 验收 | 证据 |
|------|------|
| 不配 `compactor=` 时逐字一样 | `test_without_a_compactor_the_ledger_itself_goes_to_the_model`（送给 `generate` 的**就是账本本体**，身份断言）、`test_a_counter_without_a_compactor_is_a_configuration_error`、既有 892 条零改动、事件序列快照原样通过 |
| 第 N 轮实际送模型的 messages | `_capturing_model`（`MockLLM` 的异步工厂）在 9 条接缝用例里记下「模型看到的」，如 `test_the_model_gets_the_view_while_the_ledger_stays_whole` |
| 裁剪按整轮 / 边界 | `test_trimming_keeps_whole_turns_and_stays_wire_legal`、`test_odd_tool_counts_do_not_break_the_cut`（工具结果**少于**调用数、**多于**调用数两种畸形账本各一遍）、`test_the_kept_turn_survives_an_unmatched_call` |
| system 不裁 / 留最近 1 轮 | `test_the_first_message_is_never_dropped`（身份断言 `is history[0]`）、`test_at_least_one_turn_is_kept`（`keep_recent_turns=99` + 极小水位线） |
| 工具结果截断 / 最新一轮不截 | `test_tool_results_outside_the_latest_turn_are_truncated`、`test_the_latest_turn_tool_result_is_kept_whole` |
| 不压 / 压到水位线 / 不每轮都压 | `test_under_the_threshold_nothing_is_touched`、`test_trimming_stops_under_the_watermark`、`test_keeps_the_most_turns_that_fit_the_watermark`、**`test_compacting_twice_in_a_row_does_not_happen`**（同时钉住水位线与锚：同一份账本用无锚估算器会判超阈值，用过锚的不会） |
| 滚动摘要 | `test_scrolling_summary_feeds_the_previous_summary_back`（断言送进 summarizer 的**文本**里含上一条摘要）、`test_a_summary_from_the_previous_run_keeps_scrolling`（跨 run）、`test_the_summary_rides_along_with_the_checkpoint`（跨快照 resume 回灌） |
| 摘要失败降级 | `test_summary_failure_degrades_to_trim_only`、`test_no_summarizer_degrades_to_trim_only`、`test_a_failed_summary_does_not_advance_summary_covers`、`test_a_failed_summary_is_reported_through_the_event`（这一句照样答得出来 + `warning` 有话说） |
| 摘要 token 计预算不计轮数 | `test_summarizer_tokens_count_toward_the_budget_but_not_the_turns`（`turn_count == 1`，`total_tokens == 177`） |
| Schema v4 | `test_serialized_record_matches_fixture`（v4 逐字）、`test_a_v3_snapshot_reads_back_without_a_summary`、`test_old_snapshots_report_current_schema_version`（v1~v4 都升到当前版本）、`test_v4_progress_keeps_only_resume_inputs`（8 字段）、「未来版本拒读」那条既有用例未改 |
| 事件只在真压了时发 / 终局后不发 | `test_the_compaction_event_carries_what_happened`、`test_nothing_to_compact_means_no_event`、`test_context_compacted_rejected_after_terminal`、`test_context_compacted_is_side_channel` |
| 快照仍全量 | `test_frames_before_the_compaction_stay_as_they_were`（中途才压：第一帧 `summary is None`，两帧互为前缀 —— 帧数一轮一帧、旧帧一字不变） |
| 测试与静态检查 | 框架 892 passed / 65 deselected；`-m "pg or redis or pg_db"` 51 passed / 3 skipped；业务 186 passed；`ruff check` / `ruff format --check` 干净 |
| 快照重生成是有意的 | 见「碰过的文件」最后一行：**只**动了 `checkpoint_frames_tool_path.json`（schema 3 → 4 + 两个新字段），理由写在这里 |

### 四、代码审查改了什么（两轴各起一个 sub-agent，2026-09-22）

**改掉的**：

1. **事实性说法没扫干净**（Standards 轴）：`server/utils/types.py`「事件类型是封闭的六类」、`tests/test_stream.py` 大白话版「6 种句式齐全」、`docs/difficulties/01-core-loop.md` 的「六类（已实现）」三处漏改 → 全部改成七类（第三处带日期补记）。同一轮里我曾把 DESIGN.md #4 行的「六类事件」**去掉计数**，被指出「把设计文档去具体化，与同一份 diff 里的七类自相矛盾」→ 改成「七类事件（#4 六类 + #7 压缩）」。
2. **写下了一句被本 diff 证伪的话**：模块 docstring 与 DESIGN.md 都写了「快照零改动 / `_save_checkpoint` 零改动」，而这次恰恰给 `_save_checkpoint` 加了两个字段 → 改成「账本一字不改；落盘的仍是全量历史，只多了『压到哪一步』那两个字段」。
3. **同一个词两个意思**（smell）：`_summarize(dropped=...)` 收的是**消息列表**，而 `CompiledView.dropped` 是**条数**，两块相邻 → 参数改名 `dropped_messages`。
4. **一个事实两种表示**（smell）：`_summarize` 原本返回 `(text, summarized, usage, warning)`，而 `summarized == (text is not None)` → 去掉布尔，由 `new_summary is not None` 推出。
5. **测试用裸 `ValueError`**：构造校验那条改成断言本片新导出的 `CompactionConfigError`（意图写清楚）。
6. **补了两处缺的用例**（Spec 轴）：验收点名的「工具结果多于 / 少于调用数」边界（`test_odd_tool_counts_do_not_break_the_cut` + `test_the_kept_turn_survives_an_unmatched_call`）、以及「快照仍全量：帧数只增、旧帧不变」（`test_frames_before_the_compaction_stay_as_they_were`）。

**看了但不改的（记理由）**：

- **`summary` / `summary_covers` 总是一起走**（Data Clumps，8 处）：它们就是 v4 的**持久化契约**（`CheckpointState` 本身是平铺字段的约定，`_body_payload` 逐字段列），打包成一个值类型会在编解码处多一层映射。取舍：平铺 → 少一层映射，多一处重复；沿用既有形状。
- **`CompiledView` 这个名字**：ticket 自己定的名字（`apply(...) -> CompiledView`），且模块的核心词就是「视图」；它同时带「这次做了什么」是**有意**的（事件载荷的唯一数据源，避免两处各算一次）。
- **`except Exception`**（摘要那一次调用）：与 `hooks/registry.py` / `retry/executor.py` / `tool/executor.py` 同一形状，就地写了理由（摘要不是必需件 + 不吞 `CancelledError`）；`logger.warning` 留痕 + 事件 `warning` 字段，不是静默吞。
- **`message_text` 与 `_message_tokens` 各走一遍 `tool_calls`**：两处，够不上本仓「重复 ≥ 3 次才抽取」那条线。
- **`estimated_tokens` 与 `saved_tokens` 不同尺子**：Spec 轴也点了这一条。不凑（理由见开放项表），改为在 `CompiledView` 与 `context_compacted_data` 的 docstring 里写明「别相加、都与账单不是同一个数」。

### 五、边界与交给下一片

- **前端暂时看不见压缩**：CharApp 的 `agent.html` 对认不出的事件类型是**直接忽略**（那是它有意为之的防御），所以 `context_compacted` 在页面上暂时不显示；给它加一行渲染是 **issue 20** 的事。本片按「框架提供能力」收口，不碰 `CharApp/`。
- **阈值 / 保留轮数 / 工具结果上限的具体数值归业务**：框架给了默认值（2.4 万 token 起压 / 留 2 轮 / 老工具结果截到 800 字），接线与 `CHARAPP_CONTEXT_*` 是 **issue 18**。
- **压掉的信息没丢**：旧帧还在快照里（`list_history` 可回溯）。真正的「长期记忆 + 按需检索」是另一个组件（难点 #4 情景记忆），不在本片 —— 边界已写进模块 docstring 与 DESIGN.md。
- **摘要模型默认是主模型**：想省钱可以注入一个便宜模型（`TrimAndSummarize(summarizer=...)`）；不注入就要一次真实调用 —— 这也是「摘要 token 计入预算」的由来。
