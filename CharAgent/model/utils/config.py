"""适配器共享配置: DeepSeek 默认端点与模型名规范化 (双适配器共用).

两个适配器 (client_httpx.py 裸调 / client_sdk.py openai SDK) 指向同一组默认值与模型名
处理规则, 收编于此避免常量与函数挂在任一适配器上 (兄弟模块反向依赖会
破坏「并列实现」的模块边界).
"""

from __future__ import annotations

from CharAgent.model.utils.errors import ModelConfigError

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"

# reasoning_effort 官方取值: none 关闭思考模式, low/high/max 为三档思考强度;
# minimal / medium / xhigh / ultra 是官方声明的兼容别名 (归一规则见模块下方注释)
REASONING_EFFORTS = frozenset(
    {"none", "low", "high", "max", "minimal", "medium", "xhigh", "ultra"}
)
# 关闭思考模式的 effort 取值 (与 thinking={"type": "disabled"} 等效的第二条路径)
THINKING_OFF_EFFORT = "none"


def strip_provider_prefix(model: str) -> str:
    """剥离 LangChain 风格 provider:model 前缀 (如 deepseek:deepseek-flash).

    根 .env.example 的 DEEPSEEK_MODEL_NAME 为 LangChain demo 共享, 裸调端点只接受裸名.
    """
    if ":" in model:
        _, _, name = model.partition(":")
        return name
    return model


def check_thinking_params(
    thinking: bool | None,
    reasoning_effort: str | None,
) -> None:
    """校验 thinking 与 reasoning_effort 取值合法且互不矛盾 (双适配器共用).

    官方对思考模式给了**两条独立开关路径**: ``thinking.type`` = enabled/disabled,
    以及 ``reasoning_effort`` = none (关) / low|high|max (开). 两者同时显式传入
    且方向相反时服务端行为未定义 —— 客户端 fail fast, 不把矛盾配置发出去.

    Args:
        thinking: 已解析的思考模式开关 (调用级 > 实例默认), None 表示不传.
        reasoning_effort: 已解析的思考强度, None 表示不传.

    Raises:
        ModelConfigError: effort 取值不在官方集合内; 或 thinking=False 搭配
            开启型 effort; 或 thinking=True 搭配 reasoning_effort="none".
    """
    if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
        raise ModelConfigError(
            f"reasoning_effort 取值非法: {reasoning_effort!r}, "
            f"可选: {sorted(REASONING_EFFORTS)}"
        )
    if thinking is None or reasoning_effort is None:
        return
    effort_off = reasoning_effort == THINKING_OFF_EFFORT
    if not thinking and not effort_off:
        raise ModelConfigError(
            f"thinking=False 与 reasoning_effort={reasoning_effort!r} 矛盾 "
            f"(effort 会重新打开思考模式); 二者只保留其一"
        )
    if thinking and effort_off:
        raise ModelConfigError(
            'thinking=True 与 reasoning_effort="none" 矛盾 '
            "(none 会关闭思考模式); 二者只保留其一"
        )
