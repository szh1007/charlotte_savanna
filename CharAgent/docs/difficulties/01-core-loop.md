# ① 核心循环（#1-12）

| ID | 难点 | 详细细节 | 阶段 |
|----|------|----------|------|
| 1 | 并行工具调用 + 部分失败 | • 模型一次响应可同时发起多个工具调用（`tool_calls` 是一个数组），runtime 要并发执行它们而非串行等待，缩短总耗时 | P0 |
| | | | • 并发执行可能部分成功、部分失败，失败的工具不能拖垮成功的工具，成功结果和失败原因都要一起回填给模型 | |
| | | | • 并行的结构标志是「多个 `tool_calls` 在同一个 assistant message 里」，回填或回放时必须保持这一并行语义 | |
| | | | • 如果把并行结果拆成多个顺序轮次喂回模型，模型看到的历史就变成「一次只调一个工具」，后续会退化成串行调用 | |
| 2 | 错误自纠错 | • 工具执行报错（参数错、超时、格式错）时，不是直接失败退出，而是把错误信息喂回模型，让模型看懂后重新调用 | P0 |
| | | | • 错误信息必须「可操作」：明确说「email 字段格式错误，期望 xxx@yyy」，而不是甩一个 `422 ValidationError` 给模型猜 | |
| 3 | 无限循环防护 | • `max_turns` 限制循环迭代次数，防止模型反复调用工具陷入死循环 | P0 |
| | | | • token 预算限制单次任务总 token 消耗，超出即强制停止 | |
| | | | • wall-clock 时间限制单次任务总耗时，防止长任务一直占住资源 | |
| | | | • kill switch 是运行中的紧急停止信号，能在任意时刻打断执行（区别于 max_turns 这类软限制——软限制是「这一轮结束后才判断」，kill switch 是「立刻打断」） | |
| 4 | 流式事件状态机 | • 把 agent 运行过程抽象成四类事件：thinking（思考中）/ tool_call（发起工具调用）/ tool_result（工具返回）/ final（最终答案） | P0 |
| | | | • runtime 边跑边产出事件，逐事件推给前端，用户能实时看到进度，而不是等全部跑完才一次性返回 | |
| | | | • 事件流通过 SSE 协议传输（单向推送，见选型 0005） | |
| | | | • 事件类型定为**七类**（2026-09-22 起：原六类 + #7 的 `context_compacted`）：`thinking` / `tool_call` / `tool_result` / `reasoning` / `context_compacted` / `final` / `error` —— 四类主事件之外，`reasoning` 是**旁路通道**（任意非终局位置可发，不参与主序列也不改变状态），`error` 承担异常结束（没有答复就不该有终局答复事件） | |
| | | | • 事件流是**状态机**而非日志：四条不变量在产出瞬间强校验 —— ① `seq` 每 run 从 1 单调递增（即前端 `after_event_id`）② `tool_result` 必须匹配未闭合的 `tool_call`（按 id 配对，同 id 不得开两次）③ 工具结果未回填完不得发终局事件 ④ 终局后不得再发任何事件；违反抛 `EventSequenceError`，不让乱序流进前端（这与 #10 的「`tool` 消息必须紧随带 `tool_calls` 的 `assistant`」是同一条规则在两条通道上的体现） | |
| | | | • 终局事件**恰好一个**（loop 单一出口产出）：正常作答 → `final`（content 权威值）；guard 刹车（max_turns / token 预算 / wall-clock / 截断超限，此时 `LoopResult.content=None`）、上游中断、输出被拦截 → `error`（code 取 `LoopOutcome` 值 + 事实性说明）。**降级话术（模板回复 / 转人工）归 server 层，框架不编造用户文案** | |
| | | | • `delta` 只作渐进预览、`final.content` 为权威值（前端收到即覆盖缓冲）：截断场景下（CONTINUE 跨轮拼合、CONDENSE 丢弃前缀）只累加会显示已作废内容 | |
| | | | • 文本边界：**工具轮**的助手正文归 `thinking`（它会被 loop 的拼合链剔除，属过程叙述），终止轮正文才是 `final`；无叙述的工具轮不发 `thinking`（不产空事件）。**截断轮是例外**：它的正文是答案素材（CONTINUE 段会拼进 `final`、CONDENSE 段被丢弃）—— 发 `thinking` 要么与 `final` 重复展示、要么把已作废内容推给用户，故一律不发 | |
| | | | • hook 注册表与事件总线是**两条不同通道**：`event_sink` 是事件出口（传输必须可靠，异常向上传播），`HookPoint.ON_EVENT` 是扩展点（插件异常被隔离留痕）；同一份事件喂两条通道，P2 观测无需自建出口 | |
| 5 | checkpoint 断点续跑 | • 每个 Turn 结束后，把会话状态（消息列表、当前进度）序列化成快照持久化 | P0 |
| | | | • 用 `thread_id` 分区，每个会话的状态独立存储，互不干扰 | |
| | | | • 恢复时从最近的 checkpoint 继续，关键是记录「执行到哪一步了」，从而不重复执行已完成的动作 | |
| | | | • InMemory / Redis / Postgres 三种存储语义不同：InMemory 只在内存（重启即丢）、Redis 是 KV 快照（快但弱一致）、Postgres 有事务能存历史、支持 time-travel 回溯（任意回退到历史某一时刻的 checkpoint 重新执行） | |
| | | | • 序列化选型：pickle（有安全风险、版本绑定）/ JSON（安全但类型丢失）/ msgpack（二进制紧凑） | |
| | | | • 对象不可序列化：messages 里的 tool 对象、自定义 dataclass 塞不进 JSON 时，要注册自定义序列化器 | |
| | | | • 向前兼容：checkpoint 格式升级后旧会话数据能读回（schema 版本号 + 迁移） | |
| | | | • 实现（`CharAgent/checkpoint/`）：每 Turn 结束 `AgentLoop(saver=..., thread_id=...)` 落一帧（完整历史 + 计数器 + 正文片段）；续跑 `await loop.resume(快照)` —— 以快照为起点、计数器接着数（轮数 / token 预算跨断点仍算数），已完成 Turn 的工具**不重跑**；快照停在「工具还没有结果」时先补做欠下的调用再继续（不重问模型一次，#25 的机制）。三实现的能力差异用 `saver.capabilities` 声明：内存有历史；Redis `mode="history"`（默认，Stream 流式流水账 + MAXLEN 裁剪 + TTL）有历史、`mode="latest"`（对齐 langgraph 的 ShallowRedisSaver）只留最新一帧（`load` / `history` 明确报能力错）；Postgres 一帧一行有全历史。每帧还记「观察值」（来源 loop/fork/suspension、本轮 token 与耗时、调了哪些工具），与「进度」（state）分成两块存 —— 回放调试靠它（`checkpoint/utils/history.py` 有现成的表格视图）。序列化 = JSON + 标签机制（`{"__charagent_type__": "datetime", ...}`，其余类型 `register_type` 注册）+ `schema_version` 逐级迁移（v1 裸消息列表 → v2 结构化 state；版本比当前新则拒读）。Postgres 用**同步驱动 + `asyncio.to_thread`**：psycopg 异步连接在 Windows 默认事件循环上不可用。 | |
| 6 | 强制结构化输出 | • 用 `response_format` + `json_schema` 强制模型返回符合 schema 的 JSON，而不是自由文本，便于下游程序直接消费 | P1 |
| | | | • 模型偶尔会返回不合法 JSON，需要校验；校验失败则带着校验错误信息重试 | |
| | | | • 结构化输出 vs function calling 是两种拿到结构化数据的路径，要能说清各自适用场景（前者约束最终输出格式，后者约束工具参数） | |
| 7 | 上下文压缩 | • 多轮对话会不断膨胀，逼近窗口上限前必须压缩历史 | P1 |
| | | | • 压缩方式是摘要 + 截断，但绝不能破坏 tool_calls 的结构——压缩后模型仍要能正确理解之前发生过哪些工具调用 | |
| | | | • 摘要是把早期对话总结成一段话保留大意，截断是直接丢弃更早的历史，两者可结合使用 | |
| 8 | 上下文工程 | • 窗口管理：主动决定「什么内容放进来、什么丢弃」，是被设计出来的，而非被动塞满 | P2 |
| | | | • 动态上下文组装：根据当前任务，从多个来源（用户输入、工具结果、检索片段、记忆）拼装本次上下文 | |
| | | | • 工具结果截断：超长的工具输出不能全塞进上下文，要先截断或摘要再喂给模型 | |
| | | | • lost-in-the-middle：模型对上下文中间位置的内容注意力最弱，关键信息要放开头或结尾 | |
| | | | • prompt 缓存：相同前缀的 prompt 命中缓存，省钱又提速 | |
| | | | • 区分「上下文工程」与「prompt engineering」：前者管信息怎么组织进窗口，后者管指令怎么写，这是面试常考的区别点 | |
| 9 | 意图识别 + 澄清 | • 用户表达不清、指代模糊（「那个」「它」）、需求摇摆时，不能硬猜一个方向就往下做 | P2 |
| | | | • 反问澄清机制：主动追问「你指的是 A 还是 B」来消除歧义，而不是赌 | |
| | | | • 指代消解：把「它 / 那个」还原成具体的指代对象 | |
| | | | • 多轮意图漂移：用户说着说着改了需求，agent 要能察觉并跟随，而不是死守最初意图 | |
| 10 | Tool/Function calling 协议细节 | • tool_choice 参数：auto（模型自决）/ required（必须调工具）/ none（禁止调工具）/ 指定某个工具，各自语义与使用场景 | P0 |
| | | | • parallel_tool_calls 开关：默认并行，但工具间有依赖或副作用顺序要求时强制串行 | |
| | | | • message 结构合法性：tool 消息必须紧跟对应的 assistant 带 tool_calls 消息，否则 API 报 400；上下文裁剪/压缩时必须保持配对关系 | |
| | | | • arguments 流式增量：tool_call 的 arguments 是分片 delta 累积的，要拼 JSON 而非整段返回 | |
| | | | • tool 参数 JSON schema 设计：required/optional、enum、嵌套对象、约束条件，schema 质量直接影响模型填参正确率 | |
| | | | • finish_reason 六种取值：stop（正常结束）/ tool_calls（要调工具，loop 继续）/ length（token 截断，需特殊处理）/ content_filter（被安全拦截）/ insufficient_system_resource（服务端资源不足，瞬态）/ aborted（生成被中断）。后两者是服务端中断，内容可能是半截，不能当最终答复返回（loop 判 SERVER_INTERRUPTED） | |
| | | | • length 截断时结果不完整，要么续写、要么提示模型精简，不能当正常答案返回 | |
| | | | • 续写选型：CONTINUE 用 **prompt 式续写**（前缀回填 + system 指令「接着被截断的位置继续输出」），**未采用** DeepSeek 的对话前缀续写（末条消息 `prefix: True` + `base_url=/beta`）。实测（2026-09-11，`max_tokens=100` 强造两次截断）：两处接缝均为跨消息完整句，最终 401 字零重复 —— prompt 式已够用。不采用的理由：① `base_url` 是 client 级配置，为一条边缘路径把全框架押上 beta 测试通道不划算；② 前缀续写要求末条消息为 `assistant`，与「`tool` 消息必须紧跟带 `tool_calls` 的 `assistant`」结构冲突；③ 与思考模式的交互官方未定义（定价页称 FIM 补全仅在非思考模式可用）；④ 该特性本意是**输出格式引导**（强制代码块 / JSON 开头），不是截断续写 | |
| 11 | 推理模型 reasoning 处理 | • 推理模型（DeepSeek R1 / o1）会先输出一段 reasoning_content（思维链），再输出最终答案，两者要分离处理 | P0 |
| | | | • 流式场景下 reasoning 也是增量输出，要单独作为一类事件推给前端（区别于 #4 的 thinking / tool_call） | |
| | | | • reasoning 的 token 计入成本和上下文窗口（**取 `usage.completion_tokens_details.reasoning_tokens`，不是顶层同名字段**）。**官方文档要求带 `tools` 的请求回填 wire 历史**（称缺失或为空即 400），且会被拼接进上下文；2026-09-11 实测 11 组条件（`deepseek-flash` / `deepseek-v4-pro` × 默认与 beta 端点 × httpx 裸调与官方 SDK 样例流程 × 流式与非流式 × 缺失 / 空串 / null / 部分回传四种回传形态）均未复现该 400；但不复现不等于契约不存在（官方措辞明确，触发条件可能更窄或按灰度放开），框架仍按文档执行以保留交错思考（模型跨工具调用复用推理链）。不带 `tools` 时 API 忽略该字段。原实测还覆盖 `deepseek-reasoner` / `deepseek-chat`，这两个模型名已于 2026-07-24 停用，结论不再有独立参考价值 | |
| | | | • reasoning 与 content 分属**两条通道**：前端折叠展示（Thinking 区），不混入正文。两个「历史」要分清 —— 它进模型侧 wire 上下文，也存进 Message 记录（供重连重建） | |
| | | | • 「历史膨胀」的治理靠 context compaction（上下文压缩），不靠丢弃 reasoning | |
| | | | • 非推理模型没有 reasoning_content 字段，模型层要兼容有无该字段的差异 | |
| 12 | Agent 后端数据模型 | • 核心实体：thread（会话）/ run（一次执行）/ message（消息）/ tool_call（工具调用）/ checkpoint（快照）/ event（事件） | P2 |
| | | | • 字段设计：id、tenant_id/user_id（多租户）、时间戳、状态、版本 | |
| | | | • 索引与外键：按 thread_id、run_id 查询，时间范围查询 | |
| | | | • event 表（event sourcing，事件溯源）：把每次状态变化作为不可变事件追加记录，需要时重放事件序列重建任意时刻的状态，支撑 trace 回放 | |
| | | | • TTL 与清理：会话过期清理、checkpoint 历史保留策略 | |
