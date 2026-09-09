"""ChatModel 薄协议 (SPI, ADR-0001).

MockLLM (issue 09) 与 openai SDK 适配器 (issue 02) 实现同一协议, 隔离「模型」与
runtime. messages / tools 为 wire dict 直通 /chat/completions, 不引入中间消息模型.
"""

from __future__ import annotations

from typing import Protocol

from CharAgent.model.utils.types import ModelMessage, ModelResponse, ToolSpec


class ChatModel(Protocol):
    """SPI (ADR-0001): 薄模型接入协议.

    MockLLM (issue 09) 与 openai SDK 适配器 (issue 02) 实现同一协议.
    """

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        stream: bool = False,
    ) -> ModelResponse:
        """单次模型决策调用.

        Args:
            messages: OpenAI 兼容 wire 消息列表.
            tools: JSON Schema 工具描述列表 (P0-2 @tool 产出), None 表示不开放工具.
            temperature: 采样温度, None 表示用实例默认 (服务端默认不传).
            top_p: 核采样, None 表示用实例默认 (#68).
            seed: 随机种子, 固定后同输入同输出 (#61 / #68).
            stream: True 时走 SSE 并在内部累积 delta,
                    返回与非流式相同的完整 ModelResponse.
        """
        ...

    async def aclose(self) -> None:
        """释放底层连接池."""
        ...
