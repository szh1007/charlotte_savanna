"""适配器共享配置: DeepSeek 默认端点与模型名规范化 (双适配器共用).

两个适配器 (client_httpx.py 裸调 / client_sdk.py openai SDK) 指向同一组默认值与模型名
处理规则, 收编于此避免常量与函数挂在任一适配器上 (兄弟模块反向依赖会
破坏「并列实现」的模块边界).

「模型名」这一件事在本模块有三样 (都只跟字符串有关, 与哪家适配器无关): 剥掉
`provider:` 前缀 (下面这个函数) · 默认端点与默认模型名 · **一次运行用过不止一个
模型时怎么记** (`join_model_names`, 熔断切换那种情形 —— 记名字的是 db 层的
`runs.model` 列, 但「名字怎么拼」是模型层的常识, 于是两个包都从这里取).
"""

from __future__ import annotations

from CharAgent.model.utils.errors import ModelConfigError

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"

# 一次运行用过不止一个模型时, 那几个名字怎么拼 (见 join_model_names).
# 为什么挑这个符号: 真实模型名里不会出现它 (各家只用字母 / 数字 / `-` `_` `.` `:`),
# 于是「拼过的名字」与「一个真名字」分得开 —— 价目表按名查, 拼过的查不到, 金额
# 留空并写明原因 (不给假账), 而不是把两家的价混着算.
MODEL_NAME_SEPARATOR = "+"


def join_model_names(previous: str | None, current: str | None) -> str | None:
    """两个「这一次用了哪个模型」的名字 -> 该记下的那一个 (去重 + 按首次出场保序).

    为什么需要它: 一次运行可能**用过不止一个模型** —— 模型层熔断切到备份
    (difficulties #14, 见 CharApp/docs/adr/0025), 或者一次运行分两段写同一行
    (HITL: 挂起 -> 人确认 -> 续跑). 那时记其中任何一个单独的名字都是假话, 而按
    其中一家的单价算整趟的 token 就是一笔对不上的账; 拼起来 (如 `主+备`) 是个
    **事实** —— 价目表里查不到它, 于是金额留空并写明原因: 报不出钱, 也不报错钱.

    两边都可能是**已经拼过的**名字 (第二段拿到的名字里可能已经带着前面的模型),
    所以先按分隔符拆开再合并 —— 不拆的话会拼出 `主+主+备` 这种叠字.

    Args:
        previous: 已经记着的那个名字 (运行行上 / 台账里的前几个); None 表示还没有.
        current: 这一次要记的 (单个名字, 或已经拼过的).

    Returns:
        str | None: 合并后的名字; 两边都没有 -> None.
    """
    names: list[str] = []
    for source in (previous, current):
        if not source:
            continue
        for name in source.split(MODEL_NAME_SEPARATOR):
            if name and name not in names:
                names.append(name)
    if not names:
        return None
    return MODEL_NAME_SEPARATOR.join(names)


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
