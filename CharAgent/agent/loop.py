"""AgentLoop (issue 04 / difficulties #1 #2 #10): 替模型「跑腿」的循环管家.

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
       快照 (供 checkpoint 落盘, P0-6).
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
   官方指引稍后重试 —— 但**重试不归本层** (归 P0-5 retry), 本层只如实上报,
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
- _handle_tool_turn()           工具轮: 并行执行 + 回填 + 事件 + hook
- _handle_truncation()          length 截断: 续写 / 精简 / 重试超限放弃
- _handle_server_interrupted()  上游中断: 半截内容不当答复
- _handle_completion()          自然终止: 拼合正文并结束
- _record_turn()  每轮历史快照 (供 checkpoint 落盘) + after_turn hook
- emit_terminal() 单一终局出口 (在 utils/events.py): final / error
可变状态统一收在 LoopState (utils/types.py), 逐 run 独立, 不再散落局部变量

三条输出通道 (issue 05 接上后, 一次 run 同时喂三条, 互不干扰):
1. wire 历史 (messages): 发给模型的对话, 含 reasoning_content (#11)
2. 事件流 (EventBus → event_sink): 推给前端的渐进展示 —— 每轮模型响应产
   reasoning 事件, 工具轮产 thinking + tool_call + tool_result, 收尾产恰好
   一个终局事件 (正常 final / 异常 error); 契约见 03-api.md §2
3. hook (HookRegistry): 五个生命周期触发点 (ADR-0007 扩展点), 空注册零开销

大白话版 (issue 05 给主循环加的两根线):
- 直播线 (event_sink): 主循环每干一件事就「喊一嗓子」—— 我要去查什么、查
  回来什么、最后答什么. 这些话经 EventBus 编号后推给前端, 用户就能边跑边
  看到进度, 而不是干等一分钟才蹦出一整段答案.
- 插座线 (hooks): 在 5 个固定时机顺手看一眼有没有插件要搭把手 (记记忆 /
  算钱 / 记日志), 没插就直接跳过, 不拖慢速度.
- 两条线都是「只加不改」: 不接出口、不插插件时, 主循环行为与 issue 04 时
  完全一致 (既有 233 个用例原样通过).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence

from CharAgent.agent.guard import LoopGuard
from CharAgent.agent.utils.errors import LoopConfigError
from CharAgent.agent.utils.events import (
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
        event_sink: 流式事件出口 (同步或异步回调, issue 05): 运行过程逐个推
            事件 (thinking / tool_call / tool_result / reasoning / final /
            error), 供 SSE 推送 / CLI 打印 / 测试收集. 每个 run 独立编号
            (seq 从 1 起), 终局事件恰好一个; 事件契约见 03-api.md §2.
            None 表示不接出口 (事件仍会触发 hooks 的 on_event).
        hooks: hook 注册表 (ADR-0007 扩展点, issue 05), 五个触发点与载荷见
            HookRegistry docstring; 空注册零开销. None 表示无扩展点 (内部用
            空注册表, 触发点不必逐处判空).
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
        event_sink: EventSink | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        if guard is None:
            guard = LoopGuard()
        if max_truncations < 1:
            raise LoopConfigError(f"max_truncations 必须 >= 1, 实际: {max_truncations}")
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

    async def _execute_one(self, call: ModelToolCall) -> ToolExecution:
        """执行单个工具调用: 查表 → execute_tool; 未知工具给可操作错误 (#2)."""
        tool = self._tool_map.get(call.name)
        if tool is None:
            available = ", ".join(self.tool_names) or "(未注册任何工具)"
            return ToolExecution(
                tool_name=call.name,
                ok=False,
                error=f"不存在工具 {call.name!r}; 可用工具: {available}",
            )
        return await execute_tool(tool, arguments=call.arguments)

    async def _execute_parallel(
        self, calls: Sequence[ModelToolCall]
    ) -> list[ToolExecution]:
        """并行执行同一 assistant 消息的全部 tool_call (#1).

        return_exceptions=True 表示单条失败不中断整体 (部分失败语义);
        execute_tool 自身保证永不抛异常, 该参数是防御性显式声明 ——
        父 task 被 cancel (kill switch) 时 gather 仍会取消全部子任务并在
        await 点抛 CancelledError 传播, 不会把取消吞成普通失败.
        """
        outcomes = await asyncio.gather(
            *(self._execute_one(call) for call in calls),
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

    async def run(self, messages: Sequence[ModelMessage]) -> LoopResult:
        """执行一次 agent run: while 循环直到自然结束或 guard 触发.

        本方法只做**编排** (按功能拆分后的结构, 便于逐段审查):

            guard 判定 (软限制刹车)
              → self._decide          模型决策 + before/after hook + reasoning 事件
              → 按 finish_reason 分派:
                    self._handle_tool_turn           工具轮 (并行 + 回填 + 事件)
                    self._handle_truncation          length 截断 (续写 / 精简 / 放弃)
                    self._handle_server_interrupted  上游中断 (半截不当答复)
                    self._handle_completion          自然终止 (拼合正文)
              → self._record_turn     每轮历史快照 (供 checkpoint) + after_turn hook
              → emit_terminal         单一终局出口 (final / error)

        可变状态收在 LoopState (agent/utils/types.py), 逐 run 独立.

        Args:
            messages: 初始消息历史 (wire dict, 通常为 [user] 或上次 run 的
                返回值续接); 内部拷贝, 调用方列表不被修改.

        Returns:
            LoopResult: 完整消息历史 + 结束原因 + 每轮快照.

        Raises:
            ModelError: 模型调用失败 (重试/降级属 P0-5 retry / P1-8, 本层
                不包装不吞). 此时**不发终局事件** —— 事件流中断即如实反映
                失败, 错误码与降级由调用方按 03-api.md §4 发给前端.
            asyncio.CancelledError: 外部 kill switch (task.cancel) 即时打断
                —— 本模块捕获后不做吞没处理, 直接传播 (difficulties #3);
                取消收尾的 error(cancelled) 事件由 P1-2 server 层产出.
        """
        state = LoopState(history=list(messages))
        guard = self._guard
        guard.start()
        # 事件总线与 run 同生命周期: seq 从 1 起, 状态机状态逐 run 独立
        bus = EventBus(sink=self._event_sink, hooks=self._hooks)
        tool_specs: list[ToolSpec] = [
            t.to_spec() for t in self._tool_map.values()
        ] or None

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
            await self._record_turn(state, response)

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
        """一次模型决策: hook 前后 → generate → 记账 → reasoning 事件.

        触发点: before_turn → on_model_call(before) → generate →
        on_model_call(after); kill switch 可在此 await 内即时打断
        (CancelledError 不吞, 由 run 传播).

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
        response = await self._model.generate(
            state.history,
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
            # 文本边界 (03-api.md §2.4): 工具轮的正文属过程叙述 (下面的
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

        executions = await self._execute_parallel(response.tool_calls)
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
        TurnRecord.response, 供调用方排查或重放; 重试决策不归本层 (归 P0-5 retry).
        """
        state.history.append(assistant_wire(response))
        state.outcome = LoopOutcome.SERVER_INTERRUPTED
        state.done = True

    def _handle_completion(self, state: LoopState, response: ModelResponse) -> None:
        """自然终止处理: 拼合正文并结束 run (stop 及 content_filter 等非继续信号).

        自然终止 (stop / content_filter 等非继续信号): content_filter 仅在此
        终止循环, 拦截语义由调用方依据 finish_reason 决定 (输出护栏属 P1-6).
        """
        state.history.append(assistant_wire(response))
        # 拼合续写各段为完整答复 (无截断时 content_parts 为空, 即尾段
        # 本身); 全空归一为 None, 与「无正文」语义一致 (#10)
        state.content = "".join([*state.content_parts, response.content or ""]) or None
        state.done = True

    async def _record_turn(self, state: LoopState, response: ModelResponse) -> None:
        """每 Turn 收尾: 记录完整消息历史快照 (供 checkpoint 落盘) + after_turn hook.

        每 Turn 结束记录完整消息历史快照; 浅拷贝安全: 后续轮只 append 新消息,
        不改动已有消息 dict. tokens 与 token 预算同一口径 (count_tokens 纯函数,
        无 usage 的响应计 0).
        """
        turn_elapsed_ms = self._guard.elapsed_ms
        tokens = count_tokens(response.usage)
        state.turns.append(
            TurnRecord(
                turn=state.turn_count,
                response=response,
                messages=list(state.history),
                tokens=tokens,
                elapsed_ms=turn_elapsed_ms,
            )
        )
        await self._hooks.fire(
            HookPoint.AFTER_TURN,
            turn=state.turn_count,
            response=response,
            messages=state.history,
            tokens=tokens,
            elapsed_ms=turn_elapsed_ms,
        )
