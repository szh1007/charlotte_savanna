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

循环怎么停 (两种停法, 区别很重要):
1. 模型自己停: 读完历史后决定不再调工具, 直接给出最终答复
   (finish_reason=stop) -> 正常结束, outcome=FINISHED, content=最终答复
   例外: 模型没答完就被截断 (finish_reason=length), 内容不完整不能当答案
   返回 -> 按策略续写 (CONTINUE, 保留已写前缀请模型接着写) 或精简
   (CONDENSE, 丢弃不完整内容请模型重写), 见 run() 分支与 utils/messages.py;
   重试次数超过构造参数 max_truncations 就放弃 (outcome=TRUNCATION_LIMIT)
2. 框架刹车停: 模型其实还想继续 (还要求调工具), 但轮数 / token / 时长
   预算超限, 框架不再发起下一次模型决策 -> 此时没有最终答复
   (content=None), outcome=MAX_TURNS / TOKEN_BUDGET / TIME_LIMIT,
   刹车实现见 guard.py (LoopGuard)

本文件其他要点:
- 错误自纠错 (#2): 工具失败不终止 —— 失败原因以 tool 消息回填给模型
  (execute_tool 的可操作错误文本), 模型看懂后下一轮自己换参数重试
- 消息历史是 OpenAI 兼容 wire dict, 直通 /chat/completions, 无中间模型
- 循环本体在本文件; 刹车 (LoopGuard) 在 guard.py; 类型/错误/消息构造等
  静态零件在 utils/; 公共 API 由 CharAgent/agent/__init__.py 门面导出
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence

from CharAgent.agent.guard import LoopGuard
from CharAgent.agent.utils.errors import LoopConfigError
from CharAgent.agent.utils.messages import (
    TRUNCATION_CONDENSE_TEXT,
    TRUNCATION_CONTINUE_TEXT,
    assistant_wire,
    tool_wire,
)
from CharAgent.agent.utils.types import (
    LoopOutcome,
    LoopResult,
    TruncationStrategy,
    TurnRecord,
)
from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.types import (
    FinishReason,
    ModelMessage,
    ModelToolCall,
    ToolSpec,
    Usage,
)
from CharAgent.tool import Tool, ToolExecution, execute_tool


def _count_tokens(usage: Usage | None) -> int:
    """单次响应的 token 消耗 (无 usage 的响应计 0, 如部分 mock/流式)."""
    if usage is None:
        return 0
    if usage.total_tokens is not None:
        return usage.total_tokens
    return (usage.input_tokens or 0) + (usage.output_tokens or 0)


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
            None 表示不传 (服务端默认).
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

        Args:
            messages: 初始消息历史 (wire dict, 通常为 [user] 或上次 run 的
                返回值续接); 内部拷贝, 调用方列表不被修改.

        Returns:
            LoopResult: 完整消息历史 + 结束原因 + 每轮快照.

        Raises:
            ModelError: 模型调用失败 (重试/降级属 P0-5 retry / P1-8, 本层
                不包装不吞).
            asyncio.CancelledError: 外部 kill switch (task.cancel) 即时打断
                —— 本模块捕获后不做吞没处理, 直接传播 (difficulties #3).
        """
        history: list[ModelMessage] = list(messages)
        guard = self._guard
        guard.start()

        turns: list[TurnRecord] = []
        turn_count = 0
        truncation_count = 0
        total_tokens = 0
        outcome = LoopOutcome.FINISHED
        content: str | None = None
        # CONTINUE 截断续写时已输出的正文前缀 (跨轮累积, 终止时与尾段拼合)
        content_parts: list[str] = []
        tool_specs: list[ToolSpec] = [
            t.to_spec() for t in self._tool_map.values()
        ] or None
        done = False

        while not done:
            # 软限制判定: 能走到这里说明上一轮仍需继续 (工具调用/截断);
            # 已执行完的工作与回填都留在历史里, 随时可安全中止 (该轮快照
            # 在上一轮循环底部已记录)
            if turn_count and (
                hit := guard.check_after_turn(
                    turn_count=turn_count, total_tokens=total_tokens
                )
            ):
                outcome = hit
                break

            # 1. 模型决策 (kill switch 可在此 await 内即时打断)
            response = await self._model.generate(
                history,
                tool_specs,
                temperature=self._temperature,
                top_p=self._top_p,
                seed=self._seed,
            )
            turn_count += 1
            total_tokens += (turn_tokens := _count_tokens(response.usage))

            # 2. 按 finish_reason 分支: 工具 / 截断 / 终止
            if response.has_tool_calls:
                # assistant 带 tool_calls 的消息先入历史, 保证「tool 消息
                # 紧跟对应 assistant」的配对结构 (#10); 随后整体批量回填,
                # 保持并行语义 (#1)
                #
                # 工具轮打断拼合链: 它之前的正文属过程叙述 (设计上归 thinking
                # 事件), 不是最终答复的一部分 —— 弃之, 否则会混进最终答案
                content_parts.clear()
                history.append(assistant_wire(response))
                executions = await self._execute_parallel(response.tool_calls)
                for call, execution in zip(
                    response.tool_calls, executions, strict=True
                ):
                    history.append(tool_wire(call.id, execution))
            elif response.finish_reason is FinishReason.LENGTH:
                # 截断: 内容不完整, 不能当正常答案返回 (#10)
                truncation_count += 1
                if truncation_count > self._max_truncations:
                    # 截断续写已耗尽量子: 保留截断内容于历史 (保真), 放弃
                    history.append(assistant_wire(response))
                    outcome = LoopOutcome.TRUNCATION_LIMIT
                    done = True
                elif self._truncation is TruncationStrategy.CONDENSE:
                    # 精简路径: 截断前缀不完整, 弃之 (原文仍保留在
                    # TurnRecord.response, 日志/checkpoint 可取)
                    history.append(
                        {"role": "system", "content": TRUNCATION_CONDENSE_TEXT}
                    )
                else:
                    # 续写路径: 保留截断前缀于历史, 提示接着中断处继续.
                    # 前缀同步计入 content_parts —— 最终答案跨多条 assistant
                    # 消息, 终止时由框架拼合, 调用方无需自行从 messages 提取
                    history.append(assistant_wire(response))
                    history.append(
                        {"role": "system", "content": TRUNCATION_CONTINUE_TEXT}
                    )
                    content_parts.append(response.content or "")
            else:
                # 自然终止 (stop / content_filter 等非继续信号): content_filter
                # 仅在此终止循环, 拦截语义由调用方依据 finish_reason 决定
                # (输出护栏属 P1-6)
                history.append(assistant_wire(response))
                # 拼合续写各段为完整答复 (无截断时 content_parts 为空, 即尾段
                # 本身); 全空归一为 None, 与「无正文」语义一致 (#10)
                content = "".join([*content_parts, response.content or ""]) or None
                done = True

            # 3. 每 Turn 结束记录完整消息历史快照 (供 checkpoint 落盘).
            #    浅拷贝安全: 后续轮只 append 新消息, 不改动已有消息 dict
            turns.append(
                TurnRecord(
                    turn=turn_count,
                    response=response,
                    messages=list(history),
                    tokens=turn_tokens,
                    elapsed_ms=guard.elapsed_ms,
                )
            )

        return LoopResult(
            messages=history,
            content=content,
            finish_reason=response.finish_reason,
            outcome=outcome,
            turns=turns,
            turn_count=turn_count,
            truncation_count=truncation_count,
            total_tokens=total_tokens,
            elapsed_ms=guard.elapsed_ms,
        )
