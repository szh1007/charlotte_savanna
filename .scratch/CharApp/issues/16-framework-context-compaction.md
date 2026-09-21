# 16 · 框架侧：上下文压缩（账本 / 视图分离）

**Status:** ready-for-agent

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

- [ ] **不配 `compactor=` 时行为与今天逐字一样** —— 一条既有用例都没改，且两个快照 fixture（事件序列 / 落盘形状）原样通过
- [ ] 有一条用例断言**第 N 轮实际送模型的 messages**（靠 `MockLLM` 的异步工厂，不是靠看返回值）
- [ ] 裁剪按**整轮**：绝不出现在「`assistant(tool_calls)` 留下了、它的 `tool` 消息被裁掉」的序列（含工具结果多于/少于调用数的边界用例）
- [ ] system 第一条永不裁；最近 1 轮完整保留
- [ ] 工具结果截断生效：老 `tool` 消息被截到阈值，**最新一轮不截**
- [ ] 未超阈值时**不压**；超阈值压到水位线以下；不会每轮都压（连压两次的用例）
- [ ] 滚动摘要：第二次压缩的输入含上一次摘要（用例断言送进 summarizer 的文本）
- [ ] 摘要失败 → 降级为纯裁剪，用户这一句照样答得出来，且发了 warning 事件
- [ ] 摘要的 token 计入 `total_tokens`，但**不**增加 `turn_count`
- [ ] `SCHEMA_VERSION == 4`；`checkpoint_v3.json` 读回后 `summary is None`；新增 `checkpoint_v4.json` 与落盘逐字比对；未来版本仍被拒读
- [ ] `context_compacted` 事件只在**真的压了**的时候发；终局之后不发
- [ ] `pytest CharAgent` 全绿；`ruff check` / `ruff format --check` 干净
- [ ] `UPDATE_SNAPSHOTS=1` 重生成的那两个 fixture 是**有意**变更，理由写进本 ticket

## 备注

- **为什么不挂在 `BEFORE_TURN` 钩子上**（它拿到的 `messages` 就是活引用，原地改真的能改）：① 那是 **fire 类**点，契约原话是「忽略, 没人看」——把「改变下一步输入」的逻辑塞进观察点，等于让 hook 契约变成"某些点其实可以改载荷"，正是 issue 08 拒绝给 fire 加返回值时想避免的事；② 压缩需要**框架保证的不变量**（配对、system 不裁、至少留 1 轮），这是通用知识，不该每个业务各写一遍；③ 摘要是一次真实模型调用，它的 usage 要计入运行预算，钩子点拿不到那个回写口。**如实记**：钩子那条路能跑，只是接缝选错了 —— registry 的注释是为「**注入**记忆」（加消息）写的，压缩是「**重写**」（删 / 替换），两件事
- **压掉的信息没有丢**：旧帧还在快照里（`list_history` 可回溯）。真正的「长期记忆 + 按需检索」是另一个组件（难点 #4 的情景记忆），**本片不做**，但要在文档里写清这条边界 —— 否则面试时会问「压掉的就永远没了？」
- **与 `LoopGuard` 的分工**：guard 是**运行级刹车**（这次运行总共别烧太多 → 直接停），压缩是**单请求治理**（别让这一次请求变大 → 压完继续）。两个数各自算，都要留
- 阈值 / 保留轮数 / 工具结果上限的**具体数值由业务侧定**（见 issue 18 的 `CHARAPP_CONTEXT_*`）；框架只提供能力与默认关闭
