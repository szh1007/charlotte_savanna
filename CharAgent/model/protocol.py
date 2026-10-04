"""ChatModel 薄协议 (SPI).

MockLLM 与 openai SDK 适配器实现同一协议, 隔离「模型」与
runtime. messages / tools 为 wire dict 直通 /chat/completions, 不引入中间消息模型.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from CharAgent.model.utils.types import ModelMessage, ModelResponse, ToolSpec

# 增量回调: stream=True 时每收到一块增量文本调用一次 (正文 / 思维链各一个).
# **async 且 await 到位才读下一块**: 消费者慢天然变成对上游读取的背压 —— 宁可让
# 一个慢出口把模型流拖慢, 也不要给增量开一个没人盯着的无界缓冲 (#66).
type DeltaCallback = Callable[[str], Awaitable[None]]


class ChatModel(Protocol):
    """SPI: 薄模型接入协议.

    MockLLM 与 openai SDK 适配器实现同一协议.
    """

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
        on_delta: DeltaCallback | None = None,
        on_reasoning_delta: DeltaCallback | None = None,
    ) -> ModelResponse:
        """单次模型决策调用.

        Args:
            messages: OpenAI 兼容 wire 消息列表.
            tools: JSON Schema 工具描述列表 (@tool 产出), None 表示不开放工具.
            temperature: 采样温度, None 表示用实例默认 (服务端默认不传).
                **思考模式下不生效** —— 设置不报错但被上游忽略.
            top_p: 核采样, None 表示用实例默认 (#68).
                **思考模式下下限为 0.95** (更小的值被静默抬升); 非思考模式恒为 1.0.
            seed: 随机种子, 固定后同输入同输出 (#61 / #68).
                实测限定: 思考模式下仅 content 可复现, reasoning 每次不同.
            max_tokens: 单次输出上限. 官方取值 1 ~ 384K (393216); None 表示不传
                (走上游默认: 非思考 8K, 思考 64K, effort=max 时 128K, 长输出会
                以 finish_reason=length 截断). 推理模型的思维链与正文**共享**
                该配额 (reasoning_tokens 计入 completion_tokens), 故需为思考
                留出余量.
            thinking: 思考模式开关, None 表示不传 (上游默认开启且 effort=high).
                关闭可显著省 token 与上下文, 但推理类任务质量会下降.
            reasoning_effort: 思考强度, None 表示不传 (上游默认 high). 上游按
                low/high/max 归一 (minimal/medium/xhigh/ultra 亦接受, 见映射表);
                另接受 "none" —— 与 thinking=False 等效的第二条关闭路径.
                与 thinking 同时显式传入且方向相反时报 ModelConfigError.
            stream: True 时走 SSE 并在内部累积 delta,
                    返回与非流式相同的完整 ModelResponse.
            on_delta: 正文增量回调, None 表示不要增量. **仅 stream=True 且本块
                确有 content 增量时被调用** (调用级旁路: 不改返回值, 返回给调用方
                的仍是累积完的完整响应); 拼接回调收到的全部增量, 逐字等于
                `ModelResponse.content`.
            on_reasoning_delta: 思维链增量回调, 与 on_delta **分道** —— wire 上
                正文与 reasoning_content 本来就是两个字段 (#11), 这里也不合并.
                语义同 on_delta.
        """
        ...

    async def aclose(self) -> None:
        """释放底层连接池."""
        ...
