# C26 · 增量渲染-a：模型接缝 —— `on_delta` 回调 + 吐过字不重试

**Status:** done

**Type:** feature

**Blocked by:** —

**上游:** `CharAgent/docs/DESIGN.md` #66（延迟优化：流式场景用户感知的是 TTFT，不是总延迟 —— P2 欠账，
这一片开始还）；DESIGN #4 那条「`delta` 只作渐进预览，`final.content` 才是权威值」（`DESIGN.md:115`）；
本批（C26–C29）的定案来自 2026-10-05 两轮 grilling（全文见下面「本批共同定案」）。

## 本批共同定案（C26–C29 四片共享，先读这一段）

| 决策 | 结论 | 为什么 |
|---|---|---|
| 术语 | 这件事叫**增量（delta）**，效果叫**增量渲染**；代码里事件名 `answer_delta`；**「流式」不再指前端效果** | 「流式」在这套代码里已经有两个意思：`stream=True` 是**上游传输**是不是 SSE 分块，而「流式事件」是**事件流**本身（一次运行的事件序列）。三个意思共用一个词，讨论必歧义。词条已落 `CharApp/CONTEXT.md` |
| 靶心与范围 | 答复正文 **+ 思考过程**都逐字；**验收只钉正文** | 两条增量走同一个回路（都在适配器那个 SSE 循环里），多做的成本几乎为零；过程叙述（`thinking`）与工具步骤**今天就已经是实时的**，不动 |
| 接缝 | 适配器加回调（**真流式**） | 否掉「前端拿到整段后按字符吐」：TTFT 一点没改善，那是动画不是流式（面试里一问「首字延迟多少」就穿帮）。也否掉「改异步迭代器」：要动所有调用方与录制回放，收益不值那些改动面 |
| 事件契约 | 新增 `answer_delta`（**非终局**、可重复），`final` 仍是**权威值** | DESIGN #4 早写死了这条：截断拼合（CONTINUE / CONDENSE）场景下增量里可能有**已作废**的内容，只有 `final` 算数。「终局事件恰好一个」那条不变量一个字不动。**「增量拼接 == `final.content`」是按轮成立的**（C29 真机核对后收紧口径）：工具轮那段叙述**也算增量**（用户在等工具时看得见它），但它被 `content_parts.clear()` 剔出最终答复 —— 三条设计内的不等来路就都在这里了：工具轮叙述、CONDENSE 丢弃前缀、以及（被截断直接放弃时）压根没有 `final` |
| 粒度 | 上游 chunk 里的**非空增量**原样转一帧，不额外攒 | 上游 chunk 本身就是几十毫秒一个、内含若干 token —— 天然是打字机节奏，再攒一层只会让它变钝。**空串填充块不算一帧**（上游常用 `content: ""` 的块做填充，转出去只会在前端画出空帧，见 `forward_deltas`）。留一个节流口；真机量过帧率之后**定案不做合并**（峰值 ~310 块/秒、浏览器 0 长任务，三条触发线见 C29 的「收尾记录：节流闭环」） |
| 失败语义 | **保留半截 + 一行说明**，不清屏 | 用户已经读了半截，清掉等于让这次阅读白费；而「没答完」必须说清，否则读者会以为那就是全文 |
| 重试 | **吐过字不重试**（本票的第 3 条）；吐字之前照旧重试 | 「半截 + 重来一遍」的观感比「半截 + 一句说明」糟得多。四个备选与代价记 ADR-0029（C29 落） |
| 开关 | `MinimallService.stream` 字段**默认 False**，只在服务入口 `build_service` 打开 | 服务默认开流式会让 ~80 处既有事件序列断言凭空多出 N 条事件；默认关 + 入口显式开，两头都稳 |
| 历史 | **不重放**打字机 | 历史是「记录」，逐字是「直播」；重放只会让刷新更慢，而且是**假**的（文本早就拿到了） |
| 验收 | 三条：TTFT 前后对比 / **增量拼接逐字等于 `final.content`** / 总时长不劣化 | 第 2 条是这一批的「不变量」，比「看起来在打字」重要得多 |

## 现状（读码核过，带行号）

- `ChatModel.generate(..., stream: bool = False)`（`CharAgent/model/protocol.py:20-32`）。`stream=True` 时
  两个适配器**把增量累积完再返回整段**：`client_httpx.py:230-262`（累积点 `:253`）、
  `client_sdk.py:249-271`（累积点 `:259`）—— 所以**下游谁都看不到增量**。
- **记账不受影响**（本片能不能做的前提）：流式请求会带
  `payload["stream_options"] = {"include_usage": True}`（`client_httpx.py:155-158`），
  `StreamAccumulator` 收末块 usage（`model/stream.py:116-135`）。也就是说改流式**不动** token 计量、
  成本归因（#34/#35）与 `usage_by_model`（C23/C24）。
- 生产代码**没有任何一处**传 `stream=True`：loop 从不传（`CharAgent/agent/loop.py:933-942`），
  `grep stream=True` 只命中适配器内部与测试。
- 重试嵌套是 `RetryingChatModel(FailoverChatModel(主, 备))`（`retry/__init__.py:35`，ADR-0025）。
  流中途的断连/超时被映射成**可重试**异常（`client_httpx.py:256-259`）→ 重试层**新开一个累加器整条重发**，
  而已经吐出去的半截**收不回来**。

## 本票要做的三件事

### 1. 接缝：两个具名回调

```python
DeltaCallback = Callable[[str], Awaitable[None]]

async def generate(
    self, messages, tools=None, *, ...,
    stream: bool = False,
    on_delta: DeltaCallback | None = None,             # 正文增量
    on_reasoning_delta: DeltaCallback | None = None,   # 思维链增量
) -> ModelResponse: ...
```

- **两个具名回调**而不是「一个回调带 kind」：wire 上这两条本来就是两个字段（`content` /
  `reasoning_content`），具名更好读，也不用把一个小枚举跨两个进程传。
- **async + 在 SSE 循环里 `await`**（不是同步回调 + 内部队列）：`bus.emit` 本来就是 async；
  `await` 让「消费者慢」天然变成**背压**（上游读取变慢），不引入一个没人盯着的缓冲区。
  宁可让一个慢出口把模型流拖慢，也不要内存无界。
- **两个适配器都要接**（httpx 与 sdk），且 **重试层与熔断层必须转发**
  （`retry/chat_model.py:81-114`、`retry/failover.py:126-164`）—— 它们今天只转发 `stream`，漏一个
  回调的表现是「流式开着但前端一片空白」，而那种失效**没有任何报错**。

### 2. 累积结果必须**逐字不变**

回调是旁路的：返回给 loop 的仍然是今天的完整 `ModelResponse`（`build_stream_response(accumulator)`）。
这条要有一条用例钉住 —— 它是「记账 / 落库 / 快照全都不受影响」这句话的机械证据。

### 3. 吐过字不重试

- 判据：**这一跳里 `on_delta` / `on_reasoning_delta` 被调过**（任一通道）即置位。
- 置位之后，这一跳抛出的异常**不再重试**，熔断切换也按同一条（切换同样是「整条重发」）。
- 置位是**每次尝试各自一份**（`failover` 换槽 = 新的一次尝试 = 新的位）—— 但注意：换槽时前一次的
  半截**已经吐给前端了**，所以置位之后连换槽也要停。这一点在 ADR-0029 里写清楚。
- 吐字之前失败：**照旧重试**（既有用例不许变）。

## 改了哪些文件（计划）

| 文件 | 改动 |
|---|---|
| `CharAgent/model/protocol.py` | `DeltaCallback` 类型 + 两个可选 keyword + docstring（背压与语义） |
| `CharAgent/model/client_httpx.py` | `_generate_stream` 里在 `apply_sse_chunk` 之后分发两个通道；`_payload` 不变（`stream_options` 已备） |
| `CharAgent/model/client_sdk.py` | 同上（`_accumulate_stream`） |
| `CharAgent/retry/chat_model.py` | 转发两个回调；实现「吐过字 → 不可重试」 |
| `CharAgent/retry/failover.py` | 转发两个回调；「吐过字 → 不换槽」 |
| `CharAgent/tests/mock_llm.py` | 至少**收下**两个 kwarg（不收就是 TypeError）|
| `CharAgent/tests/test_model_client_httpx.py` / `test_model_contract.py` / `test_retry*.py` | 新用例（见验收） |

## 验收

- [x] **假 SSE 回调用例**：按块、按序吐出；正文与思维链**分道**（互不串）
      —— `test_model_client_httpx.py::test_stream_forwards_deltas_on_both_channels`；
      双适配器同序列另有 `test_model_contract.py::test_stream_delta_callbacks_equivalent`
- [x] **累积结果逐字不变**：同一份假 SSE，开回调与不开回调拿到的 `ModelResponse` 逐字一致
      —— `test_stream_result_is_identical_with_and_without_callbacks`（整条 dataclass 相等）
- [x] **吐字后不重试**：第一次尝试吐了 2 块后断流 → 只发**一次**上游请求（或一次 failover 尝试）→ 异常上抛；
      对照用例：没吐字时同样的断流**照旧重试**
      —— `test_retry_chat_model.py::test_a_break_after_deltas_is_not_retried`（+ 对照
      `test_a_break_before_any_delta_is_retried_as_before`）、响应判据那半
      `test_an_interrupted_stream_after_deltas_is_not_retried`（+ 对照）；
      熔断层 `test_retry_failover.py::test_the_breaker_trips_without_switching_when_deltas_were_emitted`、
      组装形状 `test_retry_gives_up_after_the_inner_failover_emitted`
- [x] 回调为 `None`（默认）时行为与今天逐字一致（零回归）
      —— 既有 `test_retry_failover.py::test_the_parameters_are_passed_through_unchanged`
      （「包装透传的实参一字不差」）先红了一次，把「两个回调都没有时一个关键字都不传」逼进实现
- [x] `pytest CharAgent/tests` 全绿（1497 条基线）—— **1509 passed, 132 deselected**（+12 条新用例）
- [x] `pytest CharApp/tests` 全绿（本票不该动业务侧任何东西）—— **437 passed**

## 边界与不做

- **CLI 与两个非 loop 调用点不传回调**（摘要 `agent/compaction.py:846`、查询改写
  `CharApp/minimall/knowledge/query_rewrite.py:59`）—— 行为一个字不变。
- **不在这里动事件**：`answer_delta` 是 C27 的事。本票做完，`stream=True` 只是「有人能边收边拿到增量」，
  线上仍是 `stream=False`。
- **不做节流**：需要时再加（见共同定案「粒度」）。
- **不碰录制回放**：样本是非流式的（请求体里 `"stream": false`），`MockLLM.replay` 不看 stream 参数；
  但 **`RecordingChatModel` 要求非流式的 `response.raw`，流式路径从不设它** —— 以后要录新样本时，
  录制那条线必须显式关流式（写进 C29 的开关说明里）。

## 实施记录

（2026-10-04 实施。约束：**只增不改** —— 既有代码路径行为逐字不变，新增行为只在挂了回调时生效。）

**落点**

| 文件 | 改动 |
|---|---|
| `CharAgent/model/protocol.py` | `DeltaCallback` 类型 + 两个 keyword（docstring 写清「仅 stream=True 且本块确有增量」「拼接 == content」） |
| `CharAgent/model/stream.py` | **新增** `forward_deltas(chunk, *, on_delta, on_reasoning_delta)`：读与累积同一组 wire 字段、只转发不校验；空串不转；两个回调都没有时零开销 |
| `CharAgent/model/client_httpx.py` | `_generate_stream` 在 `apply_sse_chunk` **之后** `await forward_deltas(...)` |
| `CharAgent/model/client_sdk.py` | `_accumulate_stream` 同一转发点（`chunk.model_dump()` 收进变量复用） |
| `CharAgent/retry/delta_gate.py` | **新增** `DeltaGate`：转发两个回调 + 记「有没有吐过字」；两个包装共用一份规矩 |
| `CharAgent/retry/chat_model.py` | 挂 `DeltaGate`；异常判据与响应判据各加同一道吐字闸 |
| `CharAgent/retry/failover.py` | 挂 `DeltaGate`；`record_failure` 照记，吐过字则不换槽 |
| `CharAgent/model/__init__.py` / `CharAgent/__init__.py` | `DeltaCallback` 进两处门面（`test_root_facade` 守着对应关系） |
| `CharAgent/tests/mock_llm.py` | MockLLM 与 RecordingChatModel **收下**两个回调（本片不使用：逐块吐字归 C27；只钉「传了回调不 TypeError」） |

**两处与票面字面的偏离（都已改进文档/代码）**

1. **空串增量不转发**：票面共同定案原写「每个 chunk 原样转一帧」；实现按「非空增量才成帧」——
   上游用 `content: ""` 的填充块做心跳，转出去只会在前端画空帧（拼接不变量不受影响）。
   共同定案那一行已改成「非空增量原样转」，C27/C28 照新口径做。
2. **`_observing` 闭包提成 `DeltaGate`**：两个包装原本各抄一份同样的转发 + 置位逻辑，
   代码评审标注为最重的重复项；提成一个概念后两处各剩三行，且「没挂回调时一个关键字都不传」
   这条规矩只有一处。

**变异验证（把闸/顺序拆掉，确认用例真的会红）**

- 拆掉 retry 的吐字闸 → `test_a_break_after_deltas_is_not_retried`、
  `test_an_interrupted_stream_after_deltas_is_not_retried` 红（对照用例仍绿）
- 拆掉 failover 的吐字闸 → `test_the_breaker_trips_without_switching...` 红；
  两个闸都拆 → `test_retry_gives_up_after_the_inner_failover_emitted` 红（阈值调到 1，
  这一跳**本来**会跳闸换家，两个闸各自收紧才有那行断言）
- failover 只转 `on_delta` 漏 `on_reasoning_delta` → 思维链断言红（评审指出的覆盖缺口）
- 把「转发」挪到 `apply_sse_chunk` 之前 → `test_stream_malformed_chunk_is_not_forwarded` 红
  （该用例的畸形块正文是好的、坏在 `tool_calls` 分片上，否则钉不住顺序）

**评审保留项**

- 两个适配器各有一行同样的注释（「旁路转发放在累积之后」）—— 两处，低于仓库「重复 ≥3 才抽」的线，留着。
- `(on_delta, on_reasoning_delta)` 这对参数一路同行（protocol / 两个适配器 / 两个包装 / 两个 fake）：
  扁平 kwargs 与 wire 的 OpenAI 风格一致，不引入 `DeltaHandlers` 类型。

**没做的事（按票面边界）**：不动事件（`answer_delta` 是 C27）、不动业务开关（C29）、
不动 CLI 渲染、不节流、不碰录制回放（样本是非流式的）。**未提交**（等指令）。
