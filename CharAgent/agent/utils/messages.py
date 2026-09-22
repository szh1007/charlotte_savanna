"""agent loop 的消息构造、指令文案与 token 计量 (loop.py 拆出的纯函数/常量).

对齐 tool/utils/messages.py 的拆分动机 —— 行为 (AgentLoop 的 while 循环)
与「要发给模型的 wire 消息长什么样 / 用什么文案引导模型 / 一次响应记多少
token」分离:
- assistant_wire / tool_wire: 模型响应与工具执行 → 回填历史的 wire 消息
  (assistant 带 reasoning_content 与 tool_calls 的原样保真 #1/#11; tool 消息
  与 tool_call_id 配对 #10; 失败回填可操作错误文本 #2 —— 错误自纠错的载体)
- TRUNCATION_*_TEXT: length 截断后回填模型的指令 (role=system; 语义上
  指令来自框架而非用户, 防止模型把它当新请求; OpenAI/DeepSeek 兼容端点
  均允许中间 system 消息)
- count_tokens: 单次响应的 token 消耗 (guard 的 token 预算按其累计)
- estimate_tokens + SUMMARY_*: 上下文压缩 (#7) 用到的字符启发式估算与文案
  (摘要指令 / 摘要进视图时的前缀 / 摘要材料渲染). count_tokens 与它的区别是
  「量的是什么」: 前者量**一次响应的用量** (来自 API), 后者量**一份消息列表
  有多大** (本地猜) —— 权威值只有 API 给的 usage, 估算只用来判阈值.
"""

from __future__ import annotations

from collections.abc import Sequence

from CharAgent.agent.utils.types import LoopState
from CharAgent.model.utils.types import ModelMessage, ModelResponse, Usage
from CharAgent.tool import ToolExecution


def count_tokens(usage: Usage | None) -> int:
    """单次响应的 token 消耗 (无 usage 的响应计 0, 如部分 mock/流式).

    优先 total_tokens; 缺失时回退输入 + 输出之和 (上游未给总量时的兜底).
    """
    if usage is None:
        return 0
    if usage.total_tokens is not None:
        return usage.total_tokens
    return (usage.input_tokens or 0) + (usage.output_tokens or 0)


def accumulate_usage(state: LoopState, usage: Usage | None) -> None:
    """把一次响应的用量**分解**累加进 state 的五个归因计数器 (原地改).

    与 `count_tokens` 的分工: 那个给的是**一个总数** (guard 的 token 预算按它判),
    这里给的是同一个数的**分量** (成本归因按它们拆). 两份数据取自同一个 `usage`,
    所以口径必然一致 —— 分开是为了让「钱花在哪一类 token 上」答得出来: 缓存命中
    的单价远低于未命中 (官方 flash 口径下差 50 倍), 混在一个总数里看不出区别.

    三条规则都是为了**不让 None 与 0 混**:
    - `usage` 为 None: 这次没拿到用量, **什么都不加** —— 不是加 0. 加 0 会让
      「没拿到」随着轮数慢慢变成「确实是零」;
    - 某个分量为 None (上游这次没上报它): 只跳过**这一个**, 其余照加;
    - 分量有值: 累加 (计数器原为 None 时以 0 起算, 于是「报过至少一次」就有了值).

    结果因此是**已上报部分的和**: 一次运行里若有轮次没上报, 它比真实用量小. 这是
    有意的取舍 —— 宁可给一个说得清来路的偏小值, 也不给一个假装完整的数 (「这次
    没拿到」与「这次就是零」在成本归因里是相反的结论).
    """
    if usage is None:
        return
    state.input_tokens = _plus(state.input_tokens, usage.input_tokens)
    state.output_tokens = _plus(state.output_tokens, usage.output_tokens)
    state.reasoning_tokens = _plus(state.reasoning_tokens, usage.reasoning_tokens)
    state.cache_hit_tokens = _plus(state.cache_hit_tokens, usage.cache_hit_tokens)
    state.cache_miss_tokens = _plus(state.cache_miss_tokens, usage.cache_miss_tokens)


def _plus(current: int | None, increment: int | None) -> int | None:
    """累计值 (None = 一次都没上报过) + 这次的增量 (None = 这次没上报)."""
    if increment is None:
        return current
    return (current or 0) + increment


def assistant_wire(response: ModelResponse) -> ModelMessage:
    """ModelResponse → assistant wire 消息 (回填历史, 字段原样保真 #1).

    reasoning 必须回填 (#11 + DeepSeek 思考模式契约): 官方文档要求请求携带
    tools 时, 历史轮次的 reasoning_content 须完整回传 (文档称缺失即 400),
    且回传后会被拼接进上下文。2026-09-11 实测 11 组条件 (deepseek-flash /
    deepseek-v4-pro x 默认与 beta 端点, httpx 裸调与官方 SDK 样例流程,
    流式与非流式, 缺失 / 空串 / null / 部分回传四种回传形态) 均未复现该
    400 —— 但不复现不等于契约不存在 (官方文档措辞明确, 触发条件可能更窄
    或按灰度放开), 框架仍按文档执行以保留交错思考 (模型跨工具调用复用推理链).
    无 tools 时 API 忽略该字段, 故无条件携带 (保真优先, 不按请求形态分支)。

    注意 reasoning 与 content 是两条通道: 此处只负责 wire 回填, 前端折叠
    Thinking 区的展示走 reasoning 事件, 不混入 content 字段。
    """
    message: ModelMessage = {"role": "assistant", "content": response.content}
    if response.reasoning:
        message["reasoning_content"] = response.reasoning
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ]
    return message


def tool_wire(call_id: str, execution: ToolExecution) -> ModelMessage:
    """单条工具执行 → tool wire 消息 (role=tool, 与 tool_call_id 配对 #10).

    失败时回填的是可操作错误文本 (#2) —— 这正是「错误自纠错」的载体,
    模型下一轮能看到哪里错了并自行修正.
    """
    content = execution.content if execution.ok else (execution.error or "")
    return {"role": "tool", "tool_call_id": call_id, "content": content}


# 截断续写指令 (CONTINUE 策略, #10): 保留截断前缀于历史, 提示接着中断处续写
TRUNCATION_CONTINUE_TEXT = (
    "你的上一轮回答因超出输出长度限制被截断. "
    "请接着被截断的位置继续输出, 不要重复已输出的内容, "
    "直接给出剩余部分的完整回答."
)

# 截断精简指令 (CONDENSE 策略, #10): 截断前缀不完整, 提示模型精简重答
TRUNCATION_CONDENSE_TEXT = (
    "你的上一轮回答因超出输出长度限制被丢弃 (内容不完整). "
    "请以更精炼的方式重新回答, 聚焦关键信息并控制篇幅, "
    "确保能在长度限制内完成."
)


# ---------------------------------------------------------------------------
# 上下文压缩 (#7): 估算与文案
# ---------------------------------------------------------------------------

# 每条消息的固定开销 (role 标记与结构分隔符): 上游计费里这部分一直都在,
# 启发式漏掉它会把「一屏短消息」的请求估小
MESSAGE_OVERHEAD_TOKENS = 4

# 汉字的 Unicode 区段 (估算口径: 汉字一字 ≈ 一 token, 其余四字符 ≈ 一 token).
# 只用区段判断, 不引分词器 —— 中文分词器算中文同样不准, 多一个依赖只换来假精度
_CJK_RANGES: tuple[tuple[str, str], ...] = (
    ("　", "〿"),  # U+3000 ~ U+303F: CJK 标点
    ("一", "鿿"),  # U+4E00 ~ U+9FFF: CJK 统一表意文字
    ("＀", "￯"),  # U+FF00 ~ U+FFEF: 全角字母 / 数字 / 标点
)


def estimate_tokens(messages: Sequence[ModelMessage]) -> int:
    """一份消息列表**大致**多大 (字符启发式, 不调上游也不引分词器).

    只用来判阈值与水位线 —— 真正权威的输入 token 数只有上游给的 usage (#7),
    见 agent/compaction.py 的 AnchorTokenCounter.
    """
    return sum(_message_tokens(message) for message in messages)


def truncate_text(text: str, *, limit: int) -> str:
    """把一段文本截到 limit 个字符 (超出时附一句「省略了多少字」的说明).

    为什么留说明而不是只加省略号: 模型看到 \"...\" 会以为工具就返回了这么点,
    读到「省略 N 字」才知道这里被压缩过 (需要全文就再查一次, 而不是硬猜).
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(已截断, 省略 {len(text) - limit} 字)"


def message_text(message: ModelMessage, *, tool_limit: int) -> str:
    """一条 wire 消息 → 摘要材料里的一行文本 (工具正文按 tool_limit 截短).

    渲染成**纯文本**交给摘要模型: 摘要只关心「发生了什么」, 不必看懂 wire 结构,
    于是也不必保持配对 —— 这里丢掉的正是压缩要丢掉的东西.
    """
    role = str(message.get("role") or "?")
    calls = list(message.get("tool_calls") or [])
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        parts.append(
            truncate_text(content, limit=tool_limit) if role == "tool" else content
        )
    for call in calls:
        function = call.get("function") or {}
        parts.append(f"{function.get('name')}({function.get('arguments')})")
    label = f"{role}:调用工具" if calls else role
    return f"[{label}] {' '.join(parts)}"


# 摘要指令 (滚动摘要的 system 段): 只说「保留什么 / 丢掉什么」, 不教它格式 ——
# 摘要的消费者是模型自己 (进视图当 system 消息), 不是给人看的报告
SUMMARY_INSTRUCTION_TEXT = (
    "把下面的对话压成一份要点摘要, 供后续轮次回顾. "
    "保留: 用户的目标与约束、已经确认的事实 (订单号 / 金额 / 结论 / 时间)、"
    "做过的决定与原因、还没做完的事. "
    "丢掉: 寒暄与重复表述、工具返回里的格式噪音. "
    "只输出摘要正文, 不要复述这条指令, 不要加标题."
)

# 摘要进视图时的前缀: 让模型知道这段是**压缩过的** (不是某个人说的原话),
# 也让它知道更早的内容只能以此为准 (原文已不在上下文里)
SUMMARY_PREFIX_TEXT = "【更早对话的摘要 (原文已不在上下文里, 需要时以此为准)】\n"


def summary_request(
    previous_summary: str | None,
    dropped: Sequence[ModelMessage],
    *,
    tool_limit: int,
) -> list[ModelMessage]:
    """摘要那一次模型调用的输入: 指令 + (上一条摘要 + 这次新裁掉的段).

    两条都带上才是**滚动**摘要: 只压新掉的那段, 更早的信息会被逐次稀释 ——
    每次压缩都只看见一小段, 压个三五次, 开头的来龙去脉就没了.
    """
    parts: list[str] = []
    if previous_summary:
        parts.append(f"已有的摘要 (更早的对话已经压在这里):\n{previous_summary}")
    parts.append(
        "这次要压进摘要的对话:\n"
        + "\n".join(message_text(m, tool_limit=tool_limit) for m in dropped)
    )
    return [
        {"role": "system", "content": SUMMARY_INSTRUCTION_TEXT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def summary_view_message(summary: str) -> ModelMessage:
    """摘要 → 视图里的那条 system 消息 (与截断指令同一条通道约定: 框架的话)."""
    return {"role": "system", "content": SUMMARY_PREFIX_TEXT + summary}


def _message_tokens(message: ModelMessage) -> int:
    """单条消息的估算 (正文 + 工具调用参数 + 固定开销)."""
    total = MESSAGE_OVERHEAD_TOKENS
    content = message.get("content")
    if isinstance(content, str):
        total += _text_tokens(content)
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        total += _text_tokens(str(function.get("name") or ""))
        total += _text_tokens(str(function.get("arguments") or ""))
    return total


def _text_tokens(text: str) -> int:
    """一段文本 → 估算 token 数 (汉字按字算, 其余按四字符一 token 算)."""
    cjk = sum(1 for char in text if _is_cjk(char))
    return cjk + (len(text) - cjk + 3) // 4


def _is_cjk(char: str) -> bool:
    """这个字符算不算「一字一 token」的汉字 (区段见 _CJK_RANGES)."""
    return any(low <= char <= high for low, high in _CJK_RANGES)
