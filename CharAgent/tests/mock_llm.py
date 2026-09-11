"""ScriptedModel: 脚本化 fake ChatModel (difficulties #61, issue 04 先行版).

issue 04 (agent loop) 的测试需要「不依赖真实 LLM 的多轮可编排模型」——
本模块是 MockLLM (issue 09 三模式) 的先行最小实现, 覆盖其中两种模式:
- 脚本化序列: 按调用次数依次返回预设响应 (第一次调 A 第二次调 B)
- 固定返回: 脚本只放一条响应即等价

生成器另有录制回放 (真实 API 样本) 属 issue 09, 届时在本文件扩展为
正式 MockLLM 三模式, 测试文件零改动.

ScriptedModel 实现 CharAgent.model.protocol.ChatModel 协议 (Seam 1,
被测代码零改动), 每次 generate 把请求原样记录到 calls —— 轨迹断言
(#62: 断言工具调用顺序与参数、模型每轮看到了什么) 的数据源.

脚本元素: ModelResponse 或 async (messages) -> ModelResponse 的可调用
(后者用于注入轮间延时等场景); 脚本耗尽后仍被调用会显式抛错, 提示测试
脚本长度与模型实际调用次数不匹配, 而非静默返回错误结果.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from CharAgent.model.utils.types import (
    FinishReason,
    ModelMessage,
    ModelResponse,
    ModelToolCall,
    Usage,
)

# 脚本元素: 预置响应 或 按当轮 messages 动态产出的异步工厂
ScriptStep = ModelResponse | Callable[[list[ModelMessage]], Awaitable[ModelResponse]]


def text_response(
    content: str | None,
    *,
    finish_reason: FinishReason = FinishReason.STOP,
    usage: Usage | None = None,
    reasoning: str | None = None,
) -> ModelResponse:
    """纯文本响应工厂 (默认正常终止); content=None 表示无正文 (length 截断等)."""
    return ModelResponse(
        content=content,
        finish_reason=finish_reason,
        reasoning=reasoning,
        usage=usage,
        model="deepseek-v4-flash",
    )


def tool_call_response(
    *calls: ModelToolCall,
    content: str | None = None,
    finish_reason: FinishReason = FinishReason.TOOL_CALLS,
    usage: Usage | None = None,
) -> ModelResponse:
    """带 tool_calls 的响应工厂 (模型要调工具, 默认 finish=tool_calls).

    content 可同时给出 (非 None) —— 真实端点上模型常「边叙述边调工具」
    (官方思考模式样例的输出即 content="Let me check..." + tool_calls 并存),
    该形态下叙述进 wire 历史但不进最终答案。
    """
    return ModelResponse(
        content=content,
        tool_calls=list(calls),
        finish_reason=finish_reason,
        usage=usage,
        model="deepseek-v4-flash",
    )


def make_tool_call(
    name: str,
    arguments: str = "{}",
    *,
    call_id: str | None = None,
) -> ModelToolCall:
    """ModelToolCall 工厂 (arguments 为原始 JSON 字符串, 与协议保真约定一致)."""
    return ModelToolCall(id=call_id or f"call_{name}", name=name, arguments=arguments)


class ScriptedModel:
    """按脚本依次响应模型调用的 fake (Seam 1, issue 04 / #61).

    attributes:
        calls: 每次 generate 的请求记录, 每项含 messages / tools /
            temperature / top_p / seed —— 轨迹断言 (#62) 由此断言
            「模型每轮看到了什么、被要求用什么采样参数」.
        no_tools_calls: 记录 tools=None (未开放工具) 时的调用次数.
    """

    def __init__(self, script: Sequence[ScriptStep]) -> None:
        self._script: list[ScriptStep] = list(script)
        self.calls: list[dict[str, Any]] = []
        self.no_tools_calls = 0

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        stream: bool = False,
    ) -> ModelResponse:
        """ChatModel 协议实现: 记录请求 → 弹脚本下一条响应.

        同时校验协议透传约定: 声明 tools 时须收到工具列表 (直通协议);
        未声明时收到 None (两者都不该静默错位).
        """
        if tools is None:
            self.no_tools_calls += 1
        # 注意浅拷贝: 记录「模型这次看到什么」必须冻结调用时刻的历史, 否则
        # AgentLoop 后续 append 会污染先前轮次的轨迹 (列表是同一引用)
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "temperature": temperature,
                "top_p": top_p,
                "seed": seed,
                "max_tokens": max_tokens,
                "thinking": thinking,
                "reasoning_effort": reasoning_effort,
                "stream": stream,
            }
        )
        if not self._script:
            raise AssertionError(
                f"ScriptedModel 脚本已耗尽, 但模型第 {len(self.calls)} 次被调用: "
                f"请检查测试脚本条数是否与预期轮数一致 (trace 请求消息: "
                f"{messages[-1] if messages else '(空)'})"
            )
        step = self._script.pop(0)
        if callable(step):
            return await step(messages)
        return step

    async def aclose(self) -> None:
        """释放资源 (协议要求; fake 无资源可释放)."""
