# C27 · 增量渲染-b：事件 `answer_delta` + reasoning 语义收紧 + MockLLM 流式

**Status:** done

**Type:** feature

**Blocked by:** C26

**上游:** C26 的接缝（`on_delta` / `on_reasoning_delta`）；`CharAgent/docs/DESIGN.md` #4
（`delta` 只作渐进预览、`final.content` 是权威值；终局事件恰好一个）；本批共同定案见 C26 那张表。

## 现状（读码核过，带行号）

- 事件类型是八类枚举（`CharAgent/stream/utils/types.py:28-45`），终局集在三处定义
  （框架 `types.py:48-59` / BFF `app/minimall/views_bff.py:282` / 前端 `agent.html:919`）；「恰好一个终局」
  由总线落实：终局之后再发任何事件都抛 `EventSequenceError`（`stream/bus.py:150-153`）。
- **总线没有类型白名单**：`bus.py:180-181` 明写 `thinking / reasoning / context_compacted` 无状态约束，
  新类型直接流过 —— 加一类非终局事件不需要碰状态机。
- `REASONING` 今天发的是**那一轮的整段**（`agent/loop.py:926`，载荷键本来就叫 `delta`），
  `THINKING`（`:1058`）与 `FINAL`（`agent/utils/events.py:194-203`）同样整段。
- `FINAL` 的正文是**跨 CONTINUE 轮拼合**的权威值（`loop.py:1202`），工具轮的正文会被
  `content_parts.clear()`（`:1061`）剔出去 —— 所以「工具轮说过的话」**不属于**最终答复。
- 前端 `reasoning` 渲染器按 `turn` **覆盖**（`agent.html:867-873`：`turn.reasoning.set(data.turn, data.delta)`）
  —— 逐块之后不改它，框里只会剩最后一块。

## 本票要做的五件事

### 1. 新增 `answer_delta`（非终局、可重复）

```python
ANSWER_DELTA = "answer_delta"   # 正文增量 (旁路预览, final.content 才是权威值)
```

- 载荷 `{delta: str, turn: int}` —— **两条增量通道之间**同形（都是「一块文本 + 第几轮」），
  `turn` 与 `thinking` / `tool_*` 一路。**但别推广**：`thinking` 的正文键是 `message`、
  `final` 只有 `content` 没有 `turn` —— 三种事件的正文键并不通用（实施时核过 `loop.py`
  与 `agent/utils/events.py` 后改正了这条原文）。
- **不进 `TERMINAL_TYPES`**；`final` 的形状与语义一个字不动。
- 脱敏白名单**不用改**：`redaction.py` 只认 `tool_call` / `tool_result` 两类事件
  （新事件的载荷里本来就没有工具参数与返回正文）。

### 2. `REASONING` 的语义从「整段」收紧为「增量」

键名本来就是 `delta` —— 这次是**兑现它原来该有的样子**。带来的连锁：

- 同一轮会有**多条** `reasoning`；前端必须改成**追加**（C28）。
- **要核对的既有断言**：凡是「每轮一条 reasoning」或「reasoning 载荷是全文」的用例。约 40 处
  事件序列/计数断言分布在 CharAgent 12 个文件、CharApp 4 个文件（代表：`test_loop_events.py:141,186,751`、
  `test_server_app.py:295-300`、`test_stream.py:156-178`、`CharApp/tests/test_server.py:691,763`），
  开工第一步先 grep 出清单再动。

### 3. loop 把两个回调接到 `bus.emit`

| 通道 | 事件 | 今天 | 本票之后 |
|---|---|---|---|
| 正文（纯答复轮） | `answer_delta` → `final` | 只有 final 整段 | 边收边发 `answer_delta`；`final` 照旧整段（权威值） |
| 正文（工具轮） | `answer_delta` → `thinking` | 只有 thinking 整段 | **增量照发**（服务端在那一轮结束前判不出归属；由前端搬进过程行，见 C28）|
| 思维链 | `reasoning` | 每轮一条整段 | 每轮多条增量 |

**为什么工具轮的正文增量照发**：按住不发就是假流式（用户在那十几秒里看不到任何字），而「这段文本算答复
还是算过程叙述」只有**那一轮结束**时才知道。搬运是前端的事 —— 代价是那段文字会从答复区"跳"进过程区，
而它**只出现一次**。

### 4. MockLLM 加「按块吐」

- 新能力：把一条 `text_response` 按 2~4 字（可配）切块，逐块 `await on_delta`，最后返回同一条响应。
- **默认行为不变**：不传回调时与今天逐字一致 —— 跑分（`CharApp/eval/`）与全部既有用例零回归。
- `RecordingChatModel`（`CharAgent/tests/mock_llm.py:317-352`）转发两个回调。

### 5. CLI 兜底渲染

- `CharAgent/client/render.py` 的 `_TAGS` / `_COLORS`（`:48-70`）与 match（`:146-165`）各补一行 ——
  不然 CLI 跑起来会打印一行「未知事件」（`case _` 兜底在那儿，不会崩，但不该让它落到兜底）。
- **正文仍整段打**（CLI 这一片不做逐字，见 C26 共同定案）。

## 改了哪些文件（计划）

| 文件 | 改动 |
|---|---|
| `CharAgent/stream/utils/types.py` | `ANSWER_DELTA` 枚举（不进 `TERMINAL_TYPES`） |
| `CharAgent/agent/loop.py` | 两个回调 → `bus.emit`；`reasoning` 改成逐块转发 |
| `CharAgent/client/render.py` | 新事件一行渲染 |
| `CharAgent/tests/mock_llm.py` | 流式模式（切块 + 逐块回调） |
| `CharAgent/tests/test_loop_events.py` 等 | 核对既有断言 + 新用例（实做另有 `test_stream.py` / `test_mock_llm.py` / `test_client_render.py`） |
| ~~`CharApp/tests/test_bff.py`~~ | 原以为那条 handler 守护会红 —— 核过之后发现它名单是硬编码的，红不了（见验收末条）；改期到 C28 |

## 验收

- [x] **增量拼接逐字等于 `final.content`**（新用例；这是这一批的不变量）
      —— `test_loop_events.py::test_stream_answer_deltas_concat_to_the_authoritative_final`；
      两条例外也各有专门用例：CONTINUE 截断**跨轮**仍成立
      （`test_stream_deltas_concat_across_a_continue_truncation`），CONDENSE **有意不成立**
      （`test_stream_deltas_can_be_superseded_by_final_after_a_condense`，正是 DESIGN #4
      「final 覆盖缓冲」的那个场景）
- [x] 每轮 `reasoning` 变成多条；**拼接后与今天那条整段逐字一致**
      —— `test_stream_reasoning_arrives_as_increments`；反向也有：默认关时仍是每轮一条整段
      （`test_stream_off_by_default_keeps_the_old_event_shape`）
- [x] 工具轮：`answer_delta` 照发、`thinking` 照发（同一段文本出现两次 —— 由前端负责只显示一次）
      —— `test_stream_in_a_tool_round_sends_the_narrative_on_both_channels`；
      另钉「增量先到、`thinking` 后到」—— 那是 C28 搬移的判据
- [x] `pytest CharAgent/tests` 全绿（既有事件序列断言按第 2 条核对后调整）
      —— **1521 passed, 132 deselected**（1509 基线 + 12 条新用例）。实际只改了**一处**
      穷举名单：`test_stream.py::test_event_types_cover_contract`（7→8→9 类）；
      原先担心的「每轮一条 reasoning」那 40 处断言**一处都没动**（原因见实施记录）
- [x] MockLLM 默认路径零变化：`pytest CharApp/tests` 全绿（跑分装置不受影响）
      —— **437 passed**
- [x] ~~`app/minimall/tests/test_bff.py::…handler` 一条**红**（记录在案，交给 C28 转绿）~~
      —— **这条不会发生**：该用例（`test_bff.py:2217`）遍历的是一份**硬编码 8 个名字**的
      tuple，从不比对 `EventType`，所以框架加一类事件它照旧全绿。前端确实还没接
      `answer_delta`（`templates/minimall/agent.html` 里 0 处命中），但**没有任何测试拦这件事**
      —— 那张「框架加事件时页面必须做决定」的网事实上不存在。C28 已补上这项：
      先把守护改成从 `EventType` 派生，再补渲染器

## 边界与不做

- **不动终局集**、不动 `final` 载荷、不动 BFF（逐帧透传，新类型原样过）。
- **不动历史接口**：`/history` 只回 `hidden=False` 的行（`db/repositories/messages.py:258`），
  与增量无关；历史不重放打字机（共同定案）。
- **不在这里动业务开关**：`MinimallService.stream` 是 C29 的事。本票做完，线上仍是 `stream=False`，
  只有用例开着回调走完全程。
- **不做节流**。

## 实施记录

（2026-10-04 实施。约束：**只增不改** —— 既有事件序列与调用形状在开关关掉时逐字不变。）

**一个关键设计决定（与票面字面不同，但更窄且被 C29 预期）：开关放在 loop 上，默认关**

票面第 3 条只说「loop 把两个回调接到 `bus.emit`」。实现加了一个构造参数
`AgentLoop(stream=False)`：**True** 时才带 `stream=True` + 两个回调调模型；**False**（默认）
时一个关键字都不多传。于是：

- 原先担心的「40 处事件序列断言要跟着改」**根本不存在** —— 默认路径的事件流与从前逐字相同；
- C29 那句「开关关掉时两个回调都是 `None`」天然成立（业务开关从 `ChatService` 传到这个字段）；
- 只有开着的用例走增量路径。

**落点**

| 文件 | 改动 |
|---|---|
| `CharAgent/stream/utils/types.py` | `ANSWER_DELTA` 枚举（在 `REASONING` 之后，**不进** `TERMINAL_TYPES`）+ 九类口径的 docstring |
| `CharAgent/stream/bus.py` | 旁路说明与 `_advance` 的无约束名单各补一句（总线本身零逻辑改动） |
| `CharAgent/agent/loop.py` | `stream` 构造参数；`_delta_emitter(bus, type, turn)`；`_generate` 挂两个回调；流式时**不再**发事后那条整段 `reasoning` |
| `CharAgent/client/render.py` | 标签 / 颜色 / match 分支各一行 + 「正文会被打两遍」的取舍写进 docstring |
| `CharAgent/tests/mock_llm.py` | `MockLLM` 按 `delta_chunk_size`（默认 3）切块吐；`RecordingChatModel` 原样转发两个回调 |
| 计数口径 | `stream/utils/__init__.py`、`client/__init__.py`、`client/app.py`、`CharAgent/__init__.py`、`render.py` 的「七类/八类」一并订正为九类（`server/utils/types.py` 那处属 server 包，未动） |

**测试**（+12：CharAgent 1509 → 1521）

- 不变量三条：主路（拼接 == final）、CONTINUE（跨轮仍等）、CONDENSE（**有意不等** ——
  final 覆盖缓冲的理由，这条把 DESIGN #4 的话钉成了可执行的用例）
- 语义两条：流式 reasoning 是多条增量；默认关时仍是每轮一条整段 + `stream=False` 生效值
- 工具轮一条：两条通道都到、增量先到
- 总线一条：`answer_delta` 是旁路且非终局（终局之后不许再发）
- MockLLM 四条 + CLI 一条

**评审发现与处置**

| 发现 | 处置 |
|---|---|
| `_delta_emitter` 的 docstring 说「与 thinking / reasoning / final 同形，前端一个取数法」——**不实**（thinking 的键是 `message`，final 无 `turn`） | 改正 docstring 与票面第 1 条原文 |
| 验收第 6 条「BFF 守护会红」不成立（名单硬编码） | 已改票面；交接项写进 C28 |
| `extra` 名字含糊 | 改名 `stream_kwargs` |
| `RecordingChatModel` 转发时多一层 `is not None` 分拣 | 去掉，直接原样透传（它本来就该是透明包装） |
| 四个测试各自手写收集闭包 | 提成 `_DeltaRecorder`（两条通道各一个列表） |
| 「两个场景挤一条用例」 | 拆成 `test_no_chunks_without_stream` / `test_no_chunks_without_a_callback` |
| C26 留下的 `test_the_fakes_accept_the_delta_callbacks` docstring（「本片不吐增量」）已过期 | 改成「协议面」口径 |

**保留项（判断：不划算）**

- 三行渲染（`_TAGS` / `_COLORS` / match 分支）里只有 match 分支**行为可见** ——
  `_TAGS.get` 的兜底 `str(StrEnum)` 与 `_COLORS.get` 的兜底 `_DIM` 恰好等于显式填的值。
  那两行是**完整性**（九类齐）而不是可测行为，留着；评审指出的「删掉也不会红」属实。
- `(stream, on_delta, on_reasoning_delta)` 一路同行（Data Clumps）—— 扁平 kwargs 与 wire 的
  OpenAI 风格一致，C26 已记过同一条取舍。

**没做的事（按票面边界）**：不动终局集 / `final` 载荷 / BFF / 历史接口；不动业务开关（C29）；
不做节流；CLI 不做逐字（增量行 + 末尾整段，重复是当前取舍，docstring 写明）。**未提交**（等指令）。
