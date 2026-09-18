"""RetryingChatModel (difficulties #13): ChatModel 协议的重试包装.

一句话理解: 给模型套一层「自动再试」的壳 —— 上层 (agent loop / CLI / server)
拿到的仍是一个标准 ChatModel, 完全不必知道重试的存在; 壳内部在瞬态失败时按
RetryPolicy 退避重试, 重试彻底耗尽才把失败交出去.

为什么用包装而不是改 loop (「只加不改」):
- ChatModel 是薄协议 SPI, 组合一层正是它的设计用途; loop 与 model 零改动,
  既有测试用例不受影响
- 重试是「模型可用性」问题, 不是「循环控制」问题 —— 放错层会让 loop 同时背
  两个职责 (loop docstring 已声明重试决策归调用方)

两条重试判据:
- **异常判据**: is_retryable (model 层异常族自带的 retryable 标记: 429 / 5xx /
  连接失败 / 超时 = 瞬态; 其余 4xx / 配置错 / 响应畸形 = 永久)
- **响应判据**: ``finish_reason=insufficient_system_resource`` (官方指引「服务端
  资源不足, 稍后重试」) 也算一次失败尝试 —— 注意那次响应**已经计费** (#13):
  被丢弃的用量随 on_retry 的记录单 (RetryAttempt.result) 交回调用方, 供
  token 计量记账. ``aborted`` 故意不重试 (语义含糊, 可能是用户侧主动
  中断, 重试可能违背用户意图): 原样返回给 loop, 由它按既有契约上报
  SERVER_INTERRUPTED.

重试对 loop 透明 (契约不变):
- 异常路径耗尽 -> 异常上抛, loop 不吞也不发终局事件 (由 server 降级)
- 响应路径耗尽 -> 返回最后一个响应, loop 照旧判 SERVER_INTERRUPTED

``retry_upstream_interrupted=False`` 关闭响应判据 (预算硬上限 / 语义
缓存需要「不再烧一次」时用): 中断响应原样返回, 由上层决定.

大白话版: 这是「包了一层自动重试的模型」——接口一模一样, 上层照常调用; 遇到
限流 / 服务端故障 / 网络超时它会自己等一会儿再试, 试几次还是不行才报错; 遇到
参数错这类硬错误则立刻报错 (试也没用).
"""

from __future__ import annotations

from collections.abc import Sequence

from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.types import (
    FinishReason,
    ModelMessage,
    ModelResponse,
    ToolSpec,
)
from CharAgent.retry.executor import retry_async
from CharAgent.retry.policy import RetryPolicy
from CharAgent.retry.utils.types import RetryCallback


class RetryingChatModel:
    """ChatModel 协议的重试包装 (SPI 组合, difficulties #13).

    除 ``generate`` / ``aclose`` 外不新增协议方法: 上层把它当普通 ChatModel 用.

    Args:
        model: 被包装的 ChatModel (httpx 裸调 / openai SDK / MockLLM 均可).
        policy: 重试策略, None 表示 RetryPolicy() 默认值 (3 次尝试, 首次
            退避 0.5s, 总耗时预算 60s).
        retry_upstream_interrupted: 是否把「上游中断」
            (finish_reason=insufficient_system_resource) 也当瞬态重试,
            默认 True; 置 False 则原样返回 (由调用方决定是否重放).
        on_retry: 重试通知回调**序列** (可挂多个; 单回调写 ``[callback]``),
            每次「决定再试」时按序列顺序依次调用 —— token 计量 /
            观测的挂载点; 某个回调抛异常即中止其后的回调并向上抛.
    """

    def __init__(
        self,
        model: ChatModel,
        policy: RetryPolicy | None = None,
        *,
        retry_upstream_interrupted: bool = True,
        on_retry: Sequence[RetryCallback] | None = None,
    ) -> None:
        self._model = model
        self._policy = policy if policy is not None else RetryPolicy()
        self._retry_upstream_interrupted = retry_upstream_interrupted
        self._on_retry = on_retry

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        stream: bool = False,
    ) -> ModelResponse:
        """ChatModel 协议实现: 逐参数透传底层模型, 失败按策略重试 (#13).

        参数语义与 ChatModel.generate 完全一致 (见 model/protocol.py) ——
        包装不吞不改任何参数, 每次尝试带同样的采样参数 (#68).
        """
        return await retry_async(
            lambda: self._model.generate(
                messages,
                tools,
                temperature=temperature,
                top_p=top_p,
                seed=seed,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                stream=stream,
            ),
            policy=self._policy,
            retry_on_result=self._reject_response,
            on_retry=self._on_retry,
        )

    async def aclose(self) -> None:
        """释放底层连接池 (委托被包装的模型)."""
        await self._model.aclose()

    def _reject_response(self, response: ModelResponse) -> str | None:
        """结果验收判据: 返回 None = 接受; 非空字符串 = 不合格并作为重试原因.

        只认「上游中断」这一种可重试响应 —— 它是服务端侧的资源问题, 官方
        明确指引稍后重试; 其余 finish_reason (stop / tool_calls / length /
        content_filter / aborted) 都不是「再试一次就好了」的语义, 各有各的
        正式处理路径 (loop 的截断 / 拦截 / 中断分支), 不在此拦截.
        """
        if not self._retry_upstream_interrupted:
            return None
        if response.finish_reason is not FinishReason.INSUFFICIENT_SYSTEM_RESOURCE:
            return None
        tokens = response.usage.total_tokens if response.usage is not None else None
        billed = f", 该次已计费 token {tokens}" if tokens is not None else ""
        return f"上游中断 (insufficient_system_resource): 本次生成未完成{billed}"
