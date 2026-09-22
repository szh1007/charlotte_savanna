"""AgentLoop (difficulties #1 #2 #10): 替模型「跑腿」的循环管家.

一句话理解: 模型不会自己调用工具 —— AgentLoop.run() 反复做同一件事:
把「到目前为止的完整对话」交给模型, 模型读完自己决定下一步 (要调工具就
给出工具名和参数, 不想调了就给出最终答复); 框架只负责两件事: 执行模型
要求的工具调用, 把结果放回对话让模型看得见. 注意中间没有「用户再提问」:
每一轮模型看到的输入 = 用户最初的提问 + 之前所有轮次的内容 (新增的信息
主要来自工具返回结果).

一次 run 的真实过程 (具体例子: 用户问「订单 20260701123456 到哪了」):

    用户提问 [user]                            <- 唯一一次「人」的输入
    Turn 1  框架把 [user] 发给模型; 模型读完, 决定调 query_order_status
            -> 框架执行工具 -> 工具返回文本回填历史
    Turn 2  框架把 [user + 上轮工具结果] 发给模型; 模型看完结果, 决定直接答复
            -> 结束 (outcome=FINISHED)
    输出: 完整消息历史 (messages) + 最终答复 (content)

常用词对照 (面试能说清):
- Run  一次完整执行: 从用户提问到 agent 给出最终答复. 一个 run 通常由
       多个 turn 组成.
- Turn 一次模型决策 (框架把当前完整历史发给模型, 等它决策一次) + 框架
       执行它的决定. 「模型决策一次」就是一个 turn, 与调了几个工具无关:
       模型一次要求并行调 3 个工具 (同一条 assistant 消息里 3 个
       tool_calls), 3 个并发跑完一起回填, 仍然只算一个 turn —— 并行不
       增加 turn 数 (difficulties #1). 每 turn 结束会记一条 TurnRecord
       快照 (供 checkpoint 落盘).
- turn_count  整个 run 里模型决策了几次 = 几次 generate 调用.

循环怎么停 (三种停法, 区别很重要):
1. 模型自己停: 读完历史后决定不再调工具, 直接给出最终答复
   (finish_reason=stop) -> 正常结束, outcome=FINISHED, content=最终答复
   例外: 模型没答完就被截断 (finish_reason=length), 内容不完整不能当答案
   返回 -> 按策略续写 (CONTINUE, 保留已写前缀请模型接着写) 或精简
   (CONDENSE, 丢弃不完整内容请模型重写), 见 _handle_truncation 与
   utils/messages.py; 重试次数超过构造参数 max_truncations 就放弃
   (outcome=TRUNCATION_LIMIT)
2. 框架刹车停: 模型其实还想继续 (还要求调工具), 但轮数 / token / 时长
   预算超限, 框架不再发起下一次模型决策 -> 此时没有最终答复
   (content=None), outcome=MAX_TURNS / TOKEN_BUDGET / TIME_LIMIT,
   刹车实现见 guard.py (LoopGuard)
3. 上游中断停: 服务端没把这次生成跑完 (finish_reason=
   insufficient_system_resource 资源不足 / aborted 被中断) -> 内容可能是
   半截, 不能当最终答复返回, outcome=SERVER_INTERRUPTED. 资源不足属瞬态,
   官方指引稍后重试 —— 但**重试不归本层** (归重试层), 本层只如实上报,
   由调用方决定是否重放

本文件其他要点:
- 错误自纠错 (#2): 工具失败不终止 —— 失败原因以 tool 消息回填给模型
  (execute_tool 的可操作错误文本), 模型看懂后下一轮自己换参数重试
- 消息历史是 OpenAI 兼容 wire dict, 直通 /chat/completions, 无中间模型
- 循环本体在本文件; 刹车 (LoopGuard) 在 guard.py; 类型/错误/消息构造/事件
  载荷等静态零件在 utils/; 公共 API 由 CharAgent/agent/__init__.py 门面导出

主循环的拆分结构 (按功能模块拆分, 便于逐段审查):
- run()          只做编排: guard 判定 → 决策 → 分支分派 → 轮次收尾 → 终局
- _decide()      一次模型决策 (before_turn / on_model_call 前后 hook + 记账
                 + reasoning 事件)
- _compile_view() 这一轮送给模型的视图 (配了压缩策略才投影; 见下)
- _handle_tool_turn()           工具轮: 并行执行 + 回填 + 事件 + hook
- _handle_truncation()          length 截断: 续写 / 精简 / 重试超限放弃
- _handle_server_interrupted()  上游中断: 半截内容不当答复
- _handle_completion()          自然终止: 拼合正文并结束
- _record_turn()  每轮历史快照 (供 checkpoint 落盘) + after_turn hook
- emit_terminal() 单一终局出口 (在 utils/events.py): final / error
可变状态统一收在 LoopState (utils/types.py), 逐 run 独立, 不再散落局部变量

上下文压缩线 (#7, 配了 compactor 才走, 见 compaction.py):
- 账本 (state.history) append-only, 一字不改; 每次模型调用前把账本**投影**成
  一份视图 (system + 摘要 + 最近几个提问) 交给 generate. 于是会话越聊越长, 而发
  出去的请求不会跟着无限长
- 压缩不落地: 快照 / 续跑 / 回溯看到的仍是全量账本
- 真压了才发 context_compacted 事件; 摘要那一次调用的 token 计入运行预算,
  但不占 max_turns (它不是一次模型决策)

三条输出通道 (一次 run 同时喂三条, 互不干扰):
1. wire 历史 (messages): 发给模型的对话, 含 reasoning_content (#11)
2. 事件流 (EventBus → event_sink): 推给前端的渐进展示 —— 每轮模型响应产
   reasoning 事件, 工具轮产 thinking + tool_call + tool_result, 收尾产恰好
   一个终局事件 (正常 final / 异常 error)
3. hook (HookRegistry): 六个生命周期触发点 (扩展点), 空注册零开销.
   其中 before_tool_execute 是**裁决类**点: 插件在那里可以拒绝 —— 被拒的
   工具不执行, 拒绝原因当作一条工具失败回填 (走的就是下面的错误回填通道)

存档线 (依然是「只加不改」):
- 配了 saver + thread_id 时, 每 Turn 结束把进度落成一帧快照 (历史 + 计数器),
  存在哪儿由 checkpoint/ 的实现决定 (内存 / Redis 流式历史 / Postgres).
  没配就一个字节都不落, 行为与之前完全一样
- 每帧还记下「观察值」(来源 / 本轮 token 与耗时 / 调了哪些工具): 与「进度」分开
  存, 回放调试时能看出哪一步最贵、哪一帧是从老快照分叉出来的
  (checkpoint/utils/history.py 有现成的表格视图)
- 续跑走 resume(快照): 把快照里的历史与计数器当起点接着跑 —— 已经做完的事都在
  历史里, 所以不会重做 (#5). 计数器一起接续, 于是「整个 run 最多几轮 / 多少
  token」的预算跨断点仍然算数
- 快照停在「工具调用还没有结果」的半路时 (人工审批挂起点 #25), resume 先补做
  那几条调用再继续, **不重复问模型一次** —— 「从挂起点恢复而非重跑」
- 从**老**快照恢复 = time-travel: 新落的帧把 parent_id 指向那帧老快照, 历史就
  此岔出一条新分支 (#5)
- 落盘失败**向上抛** (与 event_sink 同一条规矩): 存不下快照是严重问题, 悄悄吞掉
  会变成「以为存上了」的事故. 注意与 hook 的「插件异常被隔离留痕」相反 —— hook
  是可选的旁挂插件, 快照是核心可靠性

大白话版 (给主循环加的两根线):
- 直播线 (event_sink): 主循环每干一件事就「喊一嗓子」—— 我要去查什么、查
  回来什么、最后答什么. 这些话经 EventBus 编号后推给前端, 用户就能边跑边
  看到进度, 而不是干等一分钟才蹦出一整段答案.
- 插座线 (hooks): 在 6 个固定时机顺手看一眼有没有插件要搭把手 (记记忆 /
  算钱 / 记日志 / 拦下一个工具调用), 没插就直接跳过, 不拖慢速度.
- 两条线都是「只加不改」: 不接出口、不插插件时, 主循环行为与之前
  完全一致 (既有 233 个用例原样通过).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from uuid import uuid4

from CharAgent.agent.compaction import (
    AnchorTokenCounter,
    CompactionPolicy,
    TokenCounter,
)
from CharAgent.agent.guard import LoopGuard
from CharAgent.agent.utils.errors import LoopConfigError
from CharAgent.agent.utils.events import (
    context_compacted_data,
    emit_terminal,
    tool_call_data,
    tool_result_data,
)
from CharAgent.agent.utils.messages import (
    TRUNCATION_CONDENSE_TEXT,
    TRUNCATION_CONTINUE_TEXT,
    assistant_wire,
    count_tokens,
    tool_wire,
)
from CharAgent.agent.utils.types import (
    SERVER_INTERRUPTED,
    LoopOutcome,
    LoopResult,
    LoopState,
    TruncationStrategy,
    TurnRecord,
)
from CharAgent.checkpoint.base import CheckpointSaver
from CharAgent.checkpoint.utils.pending import pending_tool_calls
from CharAgent.checkpoint.utils.types import (
    Checkpoint,
    CheckpointMetadata,
    CheckpointSource,
    CheckpointState,
    check_identifier,
)
from CharAgent.hooks.registry import HookRegistry
from CharAgent.hooks.utils.types import HookPoint, ModelCallPhase
from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.types import (
    FinishReason,
    ModelMessage,
    ModelResponse,
    ModelToolCall,
    ToolSpec,
)
from CharAgent.stream.bus import EventBus
from CharAgent.stream.utils.types import EventSink, EventType
from CharAgent.tool import Tool, ToolExecution, execute_tool


class AgentLoop:
    """手写 agent loop: 模型决策 → 并行工具 → 回填 → 循环 (difficulties 核心).

    构造参数为一次 run 的静态配置 (模型 / 工具集 / 防护 / 采样), 同一实例
    可对多组输入消息反复调用 run() —— 每次 run 内部从输入消息拷贝出新历史,
    不污染调用方列表, 也便于 server 层按 run 复用同一 AgentLoop.

    Args:
        model: ChatModel 协议实现 (httpx 裸调 / openai SDK / MockLLM).
        tools: 注册的 Tool 列表 (或 None 表示不开放工具).
        guard: 循环软限制 (max_turns / token 预算 / wall-clock), 默认
            LoopGuard() (max_turns=10, 其余不限制). 截断重试上限不属 guard
            —— 那是 AgentLoop 的截断处理语义, 见 max_truncations.
        max_truncations: 连续 length 截断允许的重试次数 (每次截断提示模型
            续写/精简算一次重试, #10); 超过则 outcome=TRUNCATION_LIMIT
            放弃 (防止小窗口下无限续写烧 token), 默认 2.
        truncation: length 截断处理策略, 默认 CONTINUE (续写).
        temperature / top_p / seed: 采样参数透传每次 generate (#68),
            None 表示不传 (上游默认). **思考模式下 temperature 不生效**,
            top_p 下限 0.95, seed 仅保证 content 可复现 (reasoning 不可复现).
        max_tokens: 单次输出上限透传 (官方取值 1 ~ 384K); 思维链与正文共享该
            配额, 设得过小会频繁触发 length 截断 (即上面的截断处理路径).
            None 表示不传 (走上游默认: 非思考 8K, 思考 64K, effort=max 时 128K).
        thinking: 思考模式开关透传, None 走上游默认 (开启, effort=high);
            关闭可省 token 但推理类任务质量下降.
        reasoning_effort: 思考强度透传 (low/high/max, 兼容别名由上游归一;
            "none" 关闭思考模式), None 走上游默认 (high). 与 thinking 同时
            显式传入且方向相反时, 模型调用期报 ModelConfigError.
        compactor: 上下文压缩策略 (#7). 给了它, 每次模型调用前先把账本投影成
            一份**视图** (摘要 + 最近几个提问) 再发出去 —— 账本本身一个字不动, 于是
            快照 / 续跑 / 回溯看到的仍是全量. None (默认) 表示不投影: 送给模型的
            就是账本本体, 行为与从前逐字一样.
        counter: 估算器 (判阈值与水位线用). 默认 AnchorTokenCounter (锚 = 上游给
            的 input_tokens). 只在配了 compactor 时有意义 —— 只给 counter 不给
            compactor 属配置写错, 构造期报 LoopConfigError.
        event_sink: 流式事件出口 (同步或异步回调): 运行过程逐个推
            事件 (thinking / tool_call / tool_result / reasoning /
            context_compacted / final / error), 供 SSE 推送 / CLI 打印 /
            测试收集. 每个 run 独立编号 (seq 从 1 起), 终局事件恰好一个.
            None 表示不接出口 (事件仍会触发 hooks 的 on_event).
        hooks: hook 注册表 (扩展点), 六个触发点与载荷见
            HookRegistry docstring; 空注册零开销. None 表示无扩展点 (内部用
            空注册表, 触发点不必逐处判空).
        saver: 快照存储 (checkpoint/ 的三实现之一). 给了它, 每 Turn
            结束就把进度落一帧; None 表示不落盘 (行为与之前完全一致).
            必须与 thread_id 一起给 —— 只给一半属于配置写错, 构造期就报错.
        thread_id: 这段对话的标识 (快照按它分区). 同一会话的多次 run 给同一个
            thread_id, 快照才串成一条链、resume 才取得到 (见 resume).
    """

    def __init__(
        self,
        model: ChatModel,
        tools: Iterable[Tool] | None = None,
        *,
        guard: LoopGuard | None = None,
        max_truncations: int = 2,
        truncation: TruncationStrategy = TruncationStrategy.CONTINUE,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        compactor: CompactionPolicy | None = None,
        counter: TokenCounter | None = None,
        event_sink: EventSink | None = None,
        hooks: HookRegistry | None = None,
        saver: CheckpointSaver | None = None,
        thread_id: str | None = None,
    ) -> None:
        if guard is None:
            guard = LoopGuard()
        if max_truncations < 1:
            raise LoopConfigError(f"max_truncations 必须 >= 1, 实际: {max_truncations}")
        if counter is not None and compactor is None:
            raise LoopConfigError(
                "counter 只在配了 compactor 时才有用 (没人会去问它估算); "
                "要压缩就两个一起给, 不压缩就一个都别给"
            )
        if (saver is None) != (thread_id is None):
            raise LoopConfigError(
                "saver 与 thread_id 必须成对给 (存到哪儿 + 属于哪段对话), "
                f"实际: saver={'已给' if saver is not None else 'None'}, "
                f"thread_id={thread_id!r}"
            )
        if thread_id is not None:
            # thread_id 会变成存储里的键名, 在这里先查一遍:
            # 装配期报错好过第一帧落盘时才炸 (那时已经在处理用户请求了)
            check_identifier("thread_id", thread_id)

        self._model = model
        self._guard = guard
        self._max_truncations = max_truncations
        self._truncation = truncation
        self._temperature = temperature
        self._top_p = top_p
        self._seed = seed
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._reasoning_effort = reasoning_effort
        self._event_sink = event_sink
        self._saver = saver
        self._thread_id = thread_id

        self._compactor = compactor
        # 估算器是压缩的零件: 没配策略就不建 (也不会有谁来问它) —— 于是「不配
        # compactor 时一个字节都不多花」这条对构造也成立
        self._counter: TokenCounter | None = None
        if compactor is not None:
            self._counter = counter if counter is not None else AnchorTokenCounter()

        # 未传注册表时用空实例 (对齐 guard=None -> LoopGuard() 的惯例):
        # 后续触发点不必逐处判空, 空注册的 fire 立即返回
        self._hooks = hooks if hooks is not None else HookRegistry()
        self._tool_map: dict[str, Tool] = {}
        for item in tools or []:
            if item.name in self._tool_map:
                raise LoopConfigError(
                    f"工具名重复: {item.name!r}, 模型无法区分同名工具"
                )
            self._tool_map[item.name] = item

    @property
    def tool_names(self) -> tuple[str, ...]:
        """已注册工具名 (测试与日志用)."""
        return tuple(self._tool_map)

    # ------------------------------------------------------------------
    # 工具执行
    # ------------------------------------------------------------------

    async def _execute_one(self, call: ModelToolCall, *, turn: int) -> ToolExecution:
        """执行单个工具调用: 查表 → 请插件裁决 → execute_tool (#2).

        未知工具 (模型幻觉) 直接给可操作错误, **不进裁决点** —— 那道门是给
        「真实存在但这次不许跑」的工具准备的, 一个不存在的工具本来就不会执行,
        没有可拦的东西 (拦截插件因此总能拿到 tool 对象, 不必判空).
        """
        tool = self._tool_map.get(call.name)
        if tool is None:
            available = ", ".join(self.tool_names) or "(未注册任何工具)"
            return ToolExecution(
                tool_name=call.name,
                ok=False,
                error=f"不存在工具 {call.name!r}; 可用工具: {available}",
            )
        # 拦截点: 请挂在 before_tool_execute 上的插件表态. 这是**框架自己的能力**
        # (工具执行前的挂载点), 与「为某个业务破例改主循环」是两回事 —— 本文件
        # 至今没有、以后也不该有一行业务判断 (业务词扫描用例守着这条线). 空注册
        # 时 decide 立即返回放行, 与从前逐字一样.
        decision = await self._hooks.decide(
            HookPoint.BEFORE_TOOL_EXECUTE, turn=turn, call=call, tool=tool
        )
        if not decision.allowed:
            # 被拒 = 这次工具调用失败, 走现成的「工具错误」通道回填 (拒绝原因是
            # Decision 的不变量: 拒绝必有原因, 见 hooks/utils/types.py)
            return ToolExecution(tool_name=call.name, ok=False, error=decision.reason)
        return await execute_tool(tool, arguments=call.arguments)

    async def _execute_parallel(
        self, calls: Sequence[ModelToolCall], *, turn: int
    ) -> list[ToolExecution]:
        """并行执行同一 assistant 消息的全部 tool_call (#1).

        return_exceptions=True 表示单条失败不中断整体 (部分失败语义);
        execute_tool 自身保证永不抛异常, 该参数是防御性显式声明 ——
        父 task 被 cancel (kill switch) 时 gather 仍会取消全部子任务并在
        await 点抛 CancelledError 传播, 不会把取消吞成普通失败.
        """
        outcomes = await asyncio.gather(
            *(self._execute_one(call, turn=turn) for call in calls),
            return_exceptions=True,
        )
        executions: list[ToolExecution] = []
        for call, outcome in zip(calls, outcomes, strict=True):
            if isinstance(outcome, ToolExecution):
                executions.append(outcome)
            else:
                # 防御分支: 单个子任务被独立取消等非预期异常 (正常不达)
                executions.append(
                    ToolExecution(
                        tool_name=call.name,
                        ok=False,
                        error="工具执行被中断, 未返回结果",
                        exception=outcome,
                    )
                )
        return executions

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(
        self,
        messages: Sequence[ModelMessage],
        *,
        run_id: str | None = None,
        summary: str | None = None,
        summary_covers: int = 0,
        parent_id: str | None = None,
    ) -> LoopResult:
        """执行一次 agent run: while 循环直到自然结束或 guard 触发.

        本方法只做**编排** (按功能拆分后的结构, 便于逐段审查):

            guard 判定 (软限制刹车)
              → self._decide          模型决策 (先投影视图) + hook + reasoning 事件
              → 按 finish_reason 分派:
                    self._handle_tool_turn           工具轮 (并行 + 回填 + 事件)
                    self._handle_truncation          length 截断 (续写 / 精简 / 放弃)
                    self._handle_server_interrupted  上游中断 (半截不当答复)
                    self._handle_completion          自然终止 (拼合正文)
              → self._record_turn     每轮快照 (TurnRecord) + 落 checkpoint + after_turn
              → emit_terminal         单一终局出口 (final / error)

        可变状态收在 LoopState (agent/utils/types.py), 逐 run 独立.
        与 resume() 的区别只在起点: 这里是全新的历史 (加上调用方递进来的压缩进度),
        那边是一帧快照.

        Args:
            messages: 初始消息历史 (wire dict, 通常为 [user] 或上次 run 的
                返回值续接); 内部拷贝, 调用方列表不被修改.
            run_id: 本次运行的编号 (存快照时写进每帧记录). None 表示生成一个
                (uuid4 hex) —— 同一个 loop 反复 run 时每次都是新编号.
            summary: 上一段运行留下的上下文摘要 (配了 compactor 才有意义);
                None 表示没有 (从头开始压). 续接上一段对话时把它一起递进来,
                于是**滚动摘要**跨 run 成立 —— 不然每段运行都会把同一段旧历史
                重压一遍 (内容不会错, 但白花一次摘要调用).
            summary_covers: 那份摘要覆盖到 messages 的第几条 (默认 0).
            parent_id: 本次运行写的第一帧快照挂在哪一帧下面 (默认 None = 新根).
                给「接着同一段会话的下一轮提问」用 (见 ChatSession 的 parent_id
                说明): 一次 run 从零起一个 LoopState, 于是同一段对话在快照存储里
                本来会**每次提问多一条新根**, 旧链变孤儿. 传上一轮的
                `LoopResult.last_checkpoint_id` 就把它们串回一条链.

        Returns:
            LoopResult: 完整消息历史 + 结束原因 + 每轮快照.

        Raises:
            ModelError: 模型调用失败 (重试/降级分层处理, 本层
                不包装不吞). 此时**不发终局事件** —— 事件流中断即如实反映
                失败, 错误码与降级由调用方发给前端.
            CheckpointError: 快照落盘失败 (配了 saver 时). 同样不吞: 存不下存档
                是可靠性故障, 必须让调用方看见 (与 event_sink 同一条规矩).
            asyncio.CancelledError: 外部 kill switch (task.cancel) 即时打断
                —— 本模块捕获后不做吞没处理, 直接传播 (difficulties #3);
                取消收尾的 error(cancelled) 事件由服务层产出.
        """
        return await self._run(
            messages,
            resume=None,
            run_id=run_id,
            summary=summary,
            summary_covers=summary_covers,
            parent_id=parent_id,
        )

    async def resume(
        self, checkpoint: Checkpoint, *, run_id: str | None = None
    ) -> LoopResult:
        """从一帧快照接着跑 (断点续跑 / time-travel 的入口, #5).

        这是「换一个起点」的 run: 把快照里的历史当输入、计数器接着数, 于是已经
        做完的事一件都不会重做 (工具结果都躺在历史里). 从**老**快照恢复时, 新落
        的几帧会把 parent_id 指向那帧老快照 —— 历史从此岔出一条新分支 (time-travel).

        两处与 run() 不同, 写在这里免得踩坑:
        - **只补做挂起点欠下的调用**: 快照停在「工具还没有结果」的半路 (人工审批
          挂起, #25) 时, 先执行那几条调用再继续, 不重复问模型一次
        - **事件序号从 1 重来**: 事件总线与 run 同生命周期 (每次 resume 都是一次
          新的执行), 续拉要看的是这一次的 seq

        Args:
            checkpoint: 起点快照 (通常来自 saver.load_latest / saver.load).
            run_id: 本次运行的编号; None 表示沿用快照里的那个 —— 「还是同一次
                运行接着跑」(想另起一次运行就显式传新编号).

        Returns:
            LoopResult: 本次执行的结果 —— turns 只含本次的轮次, turn_count 是
            含续跑前轮数的累计值 (预算判定要的正是累计口径).

        Raises:
            LoopConfigError: 本 loop 没配 saver / thread_id, 或这帧快照属于别的
                会话 (拿错存档会把两段对话搅在一起, 必须拦下).
        """
        if self._saver is None or self._thread_id is None:
            raise LoopConfigError(
                "resume 需要构造 AgentLoop 时给了 saver 与 thread_id —— "
                "没地方存档就读不出快照"
            )

        if checkpoint.thread_id != self._thread_id:
            raise LoopConfigError(
                f"这帧快照属于会话 {checkpoint.thread_id!r}, 而本 loop 的会话是 "
                f"{self._thread_id!r}: 恢复别的会话的存档会把两段对话搅在一起"
            )

        return await self._run(
            checkpoint.state.messages,
            resume=checkpoint,
            run_id=run_id or checkpoint.run_id,
        )

    async def _run(
        self,
        messages: Sequence[ModelMessage],
        *,
        resume: Checkpoint | None,
        run_id: str | None,
        summary: str | None = None,
        summary_covers: int = 0,
        parent_id: str | None = None,
    ) -> LoopResult:
        """run 与 resume 的共同实现 (差别只在起点, 以及是否补做挂起的工具调用)."""
        state = LoopState(
            history=list(messages),
            run_id=run_id or uuid4().hex,
            # 压缩进度也是「接着跑要用的」: 新 run 由调用方递进来 (跨 run 续接),
            # 从快照续跑则由 _seed_from_checkpoint 覆盖掉
            summary=summary,
            summary_covers=summary_covers,
            # 第一帧挂在哪一帧下面 (None = 新根); resume 那条路会由
            # _seed_from_checkpoint 覆盖成快照自己的编号
            last_checkpoint_id=parent_id,
        )
        if resume is not None:
            self._seed_from_checkpoint(state, resume)

        guard = self._guard
        guard.start()
        # 事件总线与 run 同生命周期: seq 从 1 起, 状态机状态逐 run 独立
        bus = EventBus(sink=self._event_sink, hooks=self._hooks)
        tool_specs: list[ToolSpec] = [
            t.to_spec() for t in self._tool_map.values()
        ] or None

        # 「这一帧怎么来的」: 从快照续跑的第一帧标 FORK, 其余按普通一轮记
        source = CheckpointSource.FORK if resume is not None else CheckpointSource.LOOP
        if resume is not None:
            # 挂起点恢复 (#25 打底): 快照停在「工具还没结果」的半路时, 先把欠的
            # 调用补做完再继续 —— 那一轮模型早就决策过了, 不必再问它一次
            pending = pending_tool_calls(state.history)
            if pending:
                await self._record_turn(
                    state,
                    await self._complete_pending_turn(state, bus, pending),
                    source=CheckpointSource.SUSPENSION,
                )
                source = CheckpointSource.LOOP

        while not state.done:
            # 软限制判定: 能走到这里说明上一轮仍需继续 (工具调用/截断);
            # 已执行完的工作与回填都留在历史里, 随时可安全中止 (该轮快照
            # 在上一轮循环底部已记录)
            if state.turn_count and (
                hit := guard.check_after_turn(
                    turn_count=state.turn_count, total_tokens=state.total_tokens
                )
            ):
                state.outcome = hit
                break

            # 1. 模型决策 (kill switch 可在此 await 内即时打断)
            response = await self._decide(state, bus, tool_specs)

            # 2. 按 finish_reason 分派: 工具 / 截断 / 上游中断 / 自然终止
            if response.has_tool_calls:
                await self._handle_tool_turn(state, bus, response)
            elif response.finish_reason is FinishReason.LENGTH:
                self._handle_truncation(state, response)
            elif response.finish_reason in SERVER_INTERRUPTED:
                self._handle_server_interrupted(state, response)
            else:
                self._handle_completion(state, response)

            # 3. 每 Turn 结束: 历史快照 (供 checkpoint 落盘) + after_turn hook
            await self._record_turn(state, response, source=source)
            # 第一帧之后一律按普通轮记 (FORK 只标「从快照长出来的那一帧」)
            source = CheckpointSource.LOOP

        result = LoopResult(
            messages=state.history,
            content=state.content,
            finish_reason=state.finish_reason,
            outcome=state.outcome,
            turns=state.turns,
            turn_count=state.turn_count,
            truncation_count=state.truncation_count,
            total_tokens=state.total_tokens,
            elapsed_ms=guard.elapsed_ms,
            # 压缩进度随结果交回调用方: 续接下一段时连同历史一起递进来 (见 run)
            summary=state.summary,
            summary_covers=state.summary_covers,
            # 最后一帧的编号: 下一轮提问拿它当 parent_id, 同一段会话的快照
            # 就串成一条链 (没配 saver 时恒为 None)
            last_checkpoint_id=state.last_checkpoint_id,
        )
        # 终局事件从 LoopResult 派生 (同一份 outcome / content / tokens /
        # elapsed_ms): 事件流与返回值不会各说各话, 且终局事件恰好一个
        await emit_terminal(bus, result)
        return result

    # ------------------------------------------------------------------
    # run 的分支方法 (按功能拆分, 结构见 run docstring)
    # ------------------------------------------------------------------

    async def _decide(
        self, state: LoopState, bus: EventBus, tool_specs: list[ToolSpec] | None
    ) -> ModelResponse:
        """一次模型决策: hook 前后 → (投影视图) → generate → 记账 → reasoning 事件.

        触发点: before_turn → on_model_call(before) → generate →
        on_model_call(after); kill switch 可在此 await 内即时打断
        (CancelledError 不吞, 由 run 传播).

        压缩 (#7) 就在这一处接上: 送给 generate 的是**视图** (配了 compactor 时),
        而账本 state.history 一个字不动 —— 快照落盘、续跑、回溯看到的都是全量.
        两个 hook 拿到的仍是账本本体 (载荷契约没变: 「插件看到的是完整历史」).

        Returns:
            ModelResponse: 本轮响应; state 上的轮次 / 累计用量 / finish_reason
            已同步更新.
        """
        turn = state.turn_count + 1
        await self._hooks.fire(
            HookPoint.BEFORE_TURN,
            turn=turn,
            messages=state.history,
            tools=tool_specs,
        )
        await self._hooks.fire(
            HookPoint.ON_MODEL_CALL,
            phase=ModelCallPhase.BEFORE,
            turn=turn,
            messages=state.history,
            tools=tool_specs,
        )
        view = await self._compile_view(state, bus)
        response = await self._model.generate(
            view,
            tool_specs,
            temperature=self._temperature,
            top_p=self._top_p,
            seed=self._seed,
            max_tokens=self._max_tokens,
            thinking=self._thinking,
            reasoning_effort=self._reasoning_effort,
        )
        state.turn_count = turn
        state.total_tokens += count_tokens(response.usage)
        state.finish_reason = response.finish_reason
        if self._counter is not None:
            # 权威值回灌 (锚): 上游说的 input_tokens 才是真的, 它记的是**账本**
            # 长度 —— 下一次估算 = 这个锚 + 之后新增那几条的启发式
            self._counter.note_usage(response.usage, message_count=len(state.history))
        await self._hooks.fire(
            HookPoint.ON_MODEL_CALL,
            phase=ModelCallPhase.AFTER,
            turn=turn,
            messages=state.history,
            tools=tool_specs,
            response=response,
            usage=response.usage,
            elapsed_ms=self._guard.elapsed_ms,
        )
        if response.reasoning:
            # reasoning 旁路通道 (#11): 思维链增量单独成事件 (前端折叠展示),
            # 不混入正文; 但它同样回填 wire 历史 (模型侧上下文, 另一条通道)
            await bus.emit(EventType.REASONING, delta=response.reasoning, turn=turn)
        return response

    async def _compile_view(
        self, state: LoopState, bus: EventBus
    ) -> list[ModelMessage]:
        """算这一轮送给模型的视图 (配了压缩策略才投影; 否则原样返回账本).

        视图是投影出来的**副本**: 压缩只影响这一次请求, `state.history` 一个字
        不动 (它仍是 append-only 的账本). 三件事顺带在这里做完:

        - 新摘要与「压到第几条」写回 state (它们是进度, 要跟着快照落盘)
        - 摘要那一次调用的 token 计入本次运行预算 (确实花了钱), 但**不加**
          turn_count —— 它不是一次模型决策, 不该占 max_turns 的额度
        - 真压了才发 context_compacted 事件 (没压就不该在前端留痕迹)
        """
        if self._compactor is None or self._counter is None:
            return state.history
        compiled = await self._compactor.apply(
            state.history,
            summary=state.summary,
            summary_covers=state.summary_covers,
            counter=self._counter,
            summarizer=self._model,
        )
        state.summary = compiled.summary
        state.summary_covers = compiled.summary_covers
        state.total_tokens += count_tokens(compiled.summarizer_usage)
        if compiled.compacted:
            await bus.emit(
                EventType.CONTEXT_COMPACTED,
                **context_compacted_data(compiled, turn=state.turn_count + 1),
            )
        return compiled.messages

    async def _handle_tool_turn(
        self, state: LoopState, bus: EventBus, response: ModelResponse
    ) -> None:
        """工具轮处理: 叙述归 thinking → assistant 入历史 → 并行执行 → 回填 + 事件.

        两条保真约定:
        - assistant 带 tool_calls 的消息**先入历史**, 保证「tool 消息紧跟对应
          assistant」的配对结构 (#10); 随后整体批量回填, 保持并行语义 (#1)
        - 工具轮打断拼合链 (content_parts.clear): 它之前的正文属过程叙述,
          不是最终答复的一部分 —— 弃之, 否则会混进最终答案
        """
        if response.content:
            # 文本边界: 工具轮的正文属过程叙述 (下面的
            # content_parts.clear() 会把它从最终答案里剔掉), 故归 thinking
            # 事件; 截断轮的正文是答案素材 (CONTINUE 进拼合链 / CONDENSE
            # 丢弃), 不作 thinking —— 否则会与 final 重复展示或把已作废内容
            # 推到用户面前
            await bus.emit(
                EventType.THINKING, message=response.content, turn=state.turn_count
            )

        state.content_parts.clear()
        state.history.append(assistant_wire(response))

        for call in response.tool_calls:
            # 工具调用事件先全发 (同一轮多条 = 并行语义, #1), 再执行
            await bus.emit(
                EventType.TOOL_CALL, **tool_call_data(call, turn=state.turn_count)
            )

        executions = await self._execute_parallel(
            response.tool_calls, turn=state.turn_count
        )
        for call, execution in zip(response.tool_calls, executions, strict=True):
            state.history.append(tool_wire(call.id, execution))
            # 结果事件按**调用顺序**(非完成顺序)产出: 并发下事件序列确定,
            # 且与历史回填顺序一致 (快照测试不 flaky)
            await bus.emit(
                EventType.TOOL_RESULT,
                **tool_result_data(call, execution, turn=state.turn_count),
            )
            await self._hooks.fire(
                HookPoint.ON_TOOL_EXECUTED,
                turn=state.turn_count,
                call=call,
                execution=execution,
            )

    def _handle_truncation(self, state: LoopState, response: ModelResponse) -> None:
        """length 截断处理 (#10): 重试超限放弃 / CONDENSE 精简重答 / CONTINUE 续写.

        截断时内容不完整, 不能当正常答案返回 —— 三条子路径:
        - 超限: 保留截断内容于历史 (保真) 后放弃 (outcome=TRUNCATION_LIMIT)
        - CONDENSE: 弃掉不完整前缀, 提示模型精简重答 (原文仍保留在
          TurnRecord.response, 日志 / checkpoint 可取)
        - CONTINUE: 保留前缀于历史 + 提示接着中断处继续; 前缀同步计入
          content_parts —— 最终答案跨多条 assistant 消息, 终止时由框架拼合,
          调用方无需自行从 messages 提取
        """
        state.truncation_count += 1
        if state.truncation_count > self._max_truncations:
            # 截断续写已耗尽量子: 保留截断内容于历史 (保真), 放弃
            state.history.append(assistant_wire(response))
            state.outcome = LoopOutcome.TRUNCATION_LIMIT
            state.done = True
        elif self._truncation is TruncationStrategy.CONDENSE:
            # 精简路径: 截断前缀不完整, 弃之 (原文仍保留在
            # TurnRecord.response, 日志/checkpoint 可取)
            state.history.append(
                {"role": "system", "content": TRUNCATION_CONDENSE_TEXT}
            )
        else:
            # 续写路径: 保留截断前缀于历史, 提示接着中断处继续.
            # 前缀同步计入 content_parts —— 最终答案跨多条 assistant
            # 消息, 终止时由框架拼合, 调用方无需自行从 messages 提取
            state.history.append(assistant_wire(response))
            state.history.append(
                {"role": "system", "content": TRUNCATION_CONTINUE_TEXT}
            )
            state.content_parts.append(response.content or "")

    def _handle_server_interrupted(
        self, state: LoopState, response: ModelResponse
    ) -> None:
        """上游中断处理: 生成被服务端打断 (资源不足 / 被中断), 半截内容不当答复.

        上游中断: 服务端没跑完这次生成 (资源不足 / 被中断), content 可能只是
        半截, 不能当最终答复返回 (content 保持 None). 半截原文仍保真入历史与
        TurnRecord.response, 供调用方排查或重放; 重试决策不归本层 (归重试层).
        """
        state.history.append(assistant_wire(response))
        state.outcome = LoopOutcome.SERVER_INTERRUPTED
        state.done = True

    def _handle_completion(self, state: LoopState, response: ModelResponse) -> None:
        """自然终止处理: 拼合正文并结束 run (stop 及 content_filter 等非继续信号).

        自然终止 (stop / content_filter 等非继续信号): content_filter 仅在此
        终止循环, 拦截语义由调用方依据 finish_reason 决定 (输出护栏属另一层).
        """
        state.history.append(assistant_wire(response))
        # 拼合续写各段为完整答复 (无截断时 content_parts 为空, 即尾段
        # 本身); 全空归一为 None, 与「无正文」语义一致 (#10)
        state.content = "".join([*state.content_parts, response.content or ""]) or None
        state.done = True

    async def _record_turn(
        self,
        state: LoopState,
        response: ModelResponse,
        *,
        source: CheckpointSource = CheckpointSource.LOOP,
    ) -> None:
        """每 Turn 收尾: 记录本轮快照 + 落 checkpoint + after_turn hook.

        每 Turn 结束记录完整消息历史快照; 浅拷贝安全: 后续轮只 append 新消息,
        不改动已有消息 dict. tokens 与 token 预算同一口径 (count_tokens 纯函数,
        无 usage 的响应计 0).

        顺序说明 (为什么先落盘、后触发 hook): 存档是可靠性基线 —— 插件晚一步收到
        通知没关系, 快照晚一步存就可能永远丢了; 落盘失败向上抛 (不吞), 而 hook
        里的插件异常由注册表隔离留痕 (两条通道的可靠性要求本来就不同).
        """
        cumulative_ms = self._guard.elapsed_ms
        # 本轮耗时 = 这次累计 - 上次累计: TurnRecord 存累计值 (供整体观察),
        # 观察值里的 turn_elapsed_ms 存增量 —— 「哪一步最慢」才比得出来
        previous_ms = state.turns[-1].elapsed_ms if state.turns else 0.0
        tokens = count_tokens(response.usage)

        state.turns.append(
            TurnRecord(
                turn=state.turn_count,
                response=response,
                messages=list(state.history),
                tokens=tokens,
                elapsed_ms=cumulative_ms,
            )
        )

        if self._saver is not None and self._thread_id is not None:
            await self._save_checkpoint(
                state,
                self._thread_id,
                metadata=self._turn_metadata(
                    state, response, source, tokens, cumulative_ms - previous_ms
                ),
            )

        await self._hooks.fire(
            HookPoint.AFTER_TURN,
            turn=state.turn_count,
            response=response,
            messages=state.history,
            tokens=tokens,
            elapsed_ms=cumulative_ms,
        )

    @staticmethod
    def _turn_metadata(
        state: LoopState,
        response: ModelResponse,
        source: CheckpointSource,
        turn_tokens: int,
        turn_elapsed_ms: float,
    ) -> CheckpointMetadata:
        """把这一轮的「观察值」装出来 (给回放调试看; 恢复不靠它).

        字段见 CheckpointMetadata 的 docstring. 两个条件取值的说明:
        - content / outcome 只在 run 真结束的那一帧写: 跑一半时它们还不成立
        - tool_names 取本轮响应的 tool_calls (并行调多个时按模型给的顺序记)
        """
        return CheckpointMetadata(
            source=source,
            turn_tokens=turn_tokens,
            turn_elapsed_ms=turn_elapsed_ms,
            tool_names=[call.name for call in response.tool_calls],
            content=state.content if state.done else None,
            finish_reason=(
                None if state.finish_reason is None else state.finish_reason.value
            ),
            outcome=state.outcome.value if state.done else None,
        )

    # ------------------------------------------------------------------
    # checkpoint 落盘与恢复 (没配 saver 时下面这些一步都不走)
    # ------------------------------------------------------------------

    @staticmethod
    def _seed_from_checkpoint(state: LoopState, checkpoint: Checkpoint) -> None:
        """把快照里的进度灌进本次 run 的工作数据 (计数器接续 + 分支起点).

        只接「接着跑要用的」: 历史已经作为输入传进来了 (resume 用
        checkpoint.state.messages 调 _run), 这里补的是计数器 / 分支链 / 压缩进度.
        刻意**不**恢复的两种值:
        - done: 从一帧「已结束」的快照恢复, 意思是「基于那一刻的历史再问一次」
          (续写 / 追问), 不是「什么都不做」—— 想跳过就别调 resume
        - content: 那是上一段 run 的答复, 属观察值; 本次 run 的 LoopResult.content
          只说本次答了什么
        """
        state.turn_count = checkpoint.state.turn_count
        state.total_tokens = checkpoint.state.total_tokens
        state.truncation_count = checkpoint.state.truncation_count
        state.content_parts = list(checkpoint.state.content_parts)
        state.summary = checkpoint.state.summary
        state.summary_covers = checkpoint.state.summary_covers
        # 新落的帧接着这帧长: 从老快照恢复时, 新帧就挂在老快照下面 (新分支)
        state.last_checkpoint_id = checkpoint.checkpoint_id

    async def _complete_pending_turn(
        self, state: LoopState, bus: EventBus, pending: list[ModelToolCall]
    ) -> ModelResponse:
        """补做完挂起时欠下的工具调用 (恢复专用), 返回还原出的那轮响应.

        场景: 快照停在「模型已经要调这几个工具、但结果还没回填」—— 人工审批的
        挂起点 (#25) 正是这种形状. 恢复时不必再问模型一次 (它那一轮早就决定过
        了), 直接把欠的调用执行掉、结果回填, 这一轮才算完; 然后循环继续往下走.

        为什么能还原出 ModelResponse: wire 历史里那条 assistant 消息就是模型当初
        说的话 (正文与 tool_calls 原样存在里面), 这里只是把它变回对象交给
        TurnRecord —— 不是编造新响应. 那一轮的 token 在挂起前那次 run 里已经记过
        账, 所以本轮计 0 (不重复计费).

        补做**不受 guard 影响**: 那是上一轮已经决定、只差一份结果的工作 (预算判定
        排在它之后) —— 欠的活先干完, 该不该继续问模型才轮到刹车说话.
        """
        turn = state.turn_count + 1
        for call in pending:
            # 事件与工具轮同序: 先全部声明「要调什么」, 再执行 (并行语义 #1)
            await bus.emit(EventType.TOOL_CALL, **tool_call_data(call, turn=turn))

        executions = await self._execute_parallel(pending, turn=turn)
        for call, execution in zip(pending, executions, strict=True):
            state.history.append(tool_wire(call.id, execution))
            await bus.emit(
                EventType.TOOL_RESULT,
                **tool_result_data(call, execution, turn=turn),
            )
            await self._hooks.fire(
                HookPoint.ON_TOOL_EXECUTED,
                turn=turn,
                call=call,
                execution=execution,
            )

        state.turn_count = turn
        return self._response_for_pending(state, pending)

    @staticmethod
    def _response_for_pending(
        state: LoopState, pending: Sequence[ModelToolCall]
    ) -> ModelResponse:
        """从历史里找回「当初那条 assistant 消息」, 还原成 ModelResponse.

        正文取那条消息里的 content (原样, 不补不加); 找不到 (历史是手拼的、形状
        不标准) 时按已知事实兜底: 这一轮只调了工具, 正文为空、结束原因是
        tool_calls.
        """
        content: str | None = None
        for message in reversed(state.history):
            if message.get("role") == "assistant" and message.get("tool_calls"):
                raw = message.get("content")
                content = raw if isinstance(raw, str) else None
                break
        return ModelResponse(
            content=content,
            tool_calls=list(pending),
            finish_reason=FinishReason.TOOL_CALLS,
        )

    async def _save_checkpoint(
        self,
        state: LoopState,
        thread_id: str,
        *,
        metadata: CheckpointMetadata,
    ) -> None:
        """把当前进度与观察值落成一帧快照 (只有配了 saver 才会走到这里).

        存两块东西 (v3 起分开):
        - 进度 (CheckpointState): 接着跑需要什么 —— 完整历史 + 计数器 + 正文片段
          + 压缩进度 (摘要与它压到第几条, v4 起)
        - 观察值 (CheckpointMetadata): 这一步发生了什么 —— 来源 / 本轮 token 与
          耗时 / 调了哪些工具 / 答了什么 / 为什么停, 给回放调试看

        注意存的是**账本** (state.history) 而不是这一轮送出去的视图: 压缩不落地
        (它是「这次请求送什么」的投影), 于是存档永远是全量, 回溯也永远是全量.

        存成功后把编号记进 state.last_checkpoint_id: 下一帧的 parent_id 指向它,
        同一会话的快照就串成一条链 (从老快照恢复时链从那里岔开, #5 time-travel).
        """
        checkpoint = Checkpoint.create(
            thread_id=thread_id,
            run_id=state.run_id,
            turn_number=state.turn_count,
            state=CheckpointState(
                messages=list(state.history),
                turn_count=state.turn_count,
                total_tokens=state.total_tokens,
                truncation_count=state.truncation_count,
                content_parts=list(state.content_parts),
                summary=state.summary,
                summary_covers=state.summary_covers,
            ),
            metadata=metadata,
            parent_id=state.last_checkpoint_id,
        )
        await self._saver.save(checkpoint)
        state.last_checkpoint_id = checkpoint.checkpoint_id
