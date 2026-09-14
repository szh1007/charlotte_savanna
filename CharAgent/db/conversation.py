"""会话消息分层: 哪几条该给前端看, 哪几条只是内部件 (issue 08 第 5 条验收).

一句话理解: agent 跑一轮会产生**一大堆消息**, 但用户只想看到「我问了什么、
它答了什么」. 本文件就是那把筛子 —— 决定每条消息该不该进前端的会话列表.

**问题有多严重** (为什么值得单独一个文件): 一次带工具的运行, wire 历史长这样:

    user      "我的订单到哪了"              <- 用户真说的
    assistant "让我先查一下" + tool_calls   <- 中间轮, 用户不关心
    tool      "订单已发货, 单号 SF123"      <- 工具回填, 用户不该看到原始 JSON
    assistant "您的订单已发货, 单号 SF123"  <- 这才是答案

    截断续写 (CONTINUE) 时还会多出: system 的「接着上面继续写」指令、以及被拆成
    好几段的 assistant 正文. 压缩摘要上线后还会有「前面聊了什么」的摘要消息.

如果把这些一股脑推给前端, 结果是: 用户会看到「让我先查一下」这种半截话, 看到
工具返回的原始数据, 甚至看到一条**冒充用户说的**消息 —— 那就不是「显示得难看」,
而是把不是用户说的话安在了用户头上. 所以这不是排版问题, 是**正确性**问题.

**筛子的规则** (全部逻辑在 `_is_visible`, 就这一处):

| wire 里的角色 | 给前端看吗 | 为什么 |
|---|---|---|
| `user` | 看 | 用户真说的话 —— 这是「谁说的」唯一可信来源 |
| `assistant` 不带 tool_calls | 看 | 它是在回答, 不是在做过程叙述 |
| `assistant` 带 tool_calls | 不看 | 那是「我要去查一下」的过程叙述, 不是答案 |
| `tool` | 不看 | 工具回填是喂模型的原料, 用户看的是结论 |
| `system` | 不看 | 续写指令 / 压缩摘要 / 角色设定 —— 系统说的, 不是人说的 |

**最终答复必须由 `LoopResult.content` 生成, 不能抄 `messages[-1].content`** ——
这是本文件最要紧的一条, 展开说:

`LoopResult.content` 是框架**拼合过**的权威答复; 而 `messages` 数组的最后一条
只是**尾段**. 截断续写 (CONTINUE) 时两者完全不同:

    messages[-1]["content"] = "巨龙..."                 <- 只是最后一段
    LoopResult.content      = "...蜿蜒如" + "巨龙..."    <- 跨段拼好的完整答复

抄尾段等于把答案拦腰截断交给用户. 所以本文件的构建函数一律**要求调用方把
`LoopResult.content` 传进来**, 而不是自己去数组末尾取. 有专门用例钉住这一点.

同理, 思维链 (reasoning) 也要**跨段拼起来**: 续写的每一段都可能带自己的
reasoning, 只留最后一段会丢掉前面的思考过程 (前端折叠展示时就是残缺的).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from CharAgent.model.utils.types import ModelMessage

# wire 消息的字段名 (与 model 层产出的一致, 见 agent/utils/messages.py)
_ROLE = "role"
_CONTENT = "content"
_REASONING = "reasoning_content"
_TOOL_CALLS = "tool_calls"

# 角色取值 (与 db.entities.MessageRole 一一对应; 这里用裸字符串是为了不依赖实体模块
# —— 本文件是纯逻辑, 拿一段字典就能测)
_ROLE_USER = "user"
_ROLE_ASSISTANT = "assistant"


@dataclass(slots=True)
class TranscriptLine:
    """wire 历史里的一条消息 + 「该不该给前端看」的标记.

    这是个**中间产物**: 数据库实体 (db.entities.Message) 需要 thread_id / message_id
    这些本文件给不出的东西, 所以这里只算「内容与可见性」, 编号与归属由仓储补.

    attributes:
        role: 发言者 (`user` / `assistant` / `tool` / `system`).
        content: 文本内容 (可能为 None —— 只调工具不说话的中间轮就是这样).
        reasoning: 思维链 (wire 里的 reasoning_content), 没有则 None.
        tool_call_ids: 这条消息发起了哪些工具调用 (空列表表示没调).
        hidden: **是否对前端隐藏** —— 唯一的可见性开关, 语义见模块 docstring.
    """

    role: str
    content: str | None
    reasoning: str | None
    tool_call_ids: list[str] = field(default_factory=list)
    hidden: bool = False

    @property
    def visible(self) -> bool:
        """给人读的正向表述 (`if line.visible` 比 `if not line.hidden` 好读)."""
        return not self.hidden


@dataclass(slots=True)
class TurnPair:
    """一问一答: 该存进会话历史的那两条消息的内容.

    一段对话 = 若干个 TurnPair 按时间排起来. 每条 TurnPair 落库时变成**两行**:
    一行 `role=user` 的问题 + 一行 `role=assistant` 的回答 (两条都是可见的).

    attributes:
        question: 用户真发的那句话.
        answer: 最终答复正文 —— 来自 `LoopResult.content` (**不是** messages 数组
            的最后一条, 理由见模块 docstring). 没答出正文时为 None (guard 刹车 /
            上游中断那种收尾).
        reasoning: 回答过程中攒下的思维链 (跨段拼合); 没有则 None.
    """

    question: str
    answer: str | None
    reasoning: str | None = None


def visible_transcript(
    messages: Sequence[ModelMessage],
    *,
    since: int = 0,
) -> list[TranscriptLine]:
    """把 wire 消息历史逐条打上「给不给前端看」的标记.

    Args:
        messages: 完整 wire 消息历史 (`LoopResult.messages`), 或它的一个切片.
        since: 从第几条开始算 (0 = 从头). 用来只处理**本次新增**的那一段:
            调用方知道「跑之前历史有多长」, 传进来即可 —— 老的那部分上一次已经
            存过了, 重存一遍会写出重复行.

    Returns:
        list[TranscriptLine]: 与输入顺序一致, 每条一个标记.

    说明: 不是 `user` 发起的消息一律标 `hidden=True`; `assistant` 里只有**不带
    工具调用**的才可见 (它在回答而不是在做过程叙述). 真正要写进会话历史的那
    两条由 `conversation_turns` 给出 —— 它会把多段续写的思维链拼起来.
    """
    if since < 0:
        since = 0
    picked = list(messages[since:]) if since else list(messages)
    return [_line_of(message) for message in picked]


def _line_of(message: ModelMessage) -> TranscriptLine:
    """一条 wire 消息 → TranscriptLine.

    为什么单独一个函数: 判定规则只有一份, 改动时只有这一处要动; 用例也可以
    直接喂单条消息验证规则, 不必每次都造一整段历史.
    """
    if not isinstance(message, dict):
        # 历史可能由调用方手工拼出来 (测试里造样本), 一条不是字典的脏数据不该
        # 让整段会话读不出来 —— 当作「看不懂的条目」藏起来, 别推给前端.
        return TranscriptLine(
            role="", content=None, reasoning=None, tool_call_ids=[], hidden=True
        )

    raw_role = message.get(_ROLE)
    role = raw_role if isinstance(raw_role, str) else ""
    raw_content = message.get(_CONTENT)
    raw_reasoning = message.get(_REASONING)
    call_ids = _tool_call_ids(message)

    return TranscriptLine(
        role=role,
        content=raw_content if isinstance(raw_content, str) else None,
        reasoning=raw_reasoning if isinstance(raw_reasoning, str) else None,
        tool_call_ids=call_ids,
        hidden=not _is_visible(role, call_ids),
    )


def _is_visible(role: str, tool_call_ids: list[str]) -> bool:
    """这条消息该不该给前端看 (规则表见模块 docstring).

    Args:
        role: 发言者.
        tool_call_ids: 它发起的工具调用 (空列表 = 没调).

    Returns:
        bool: 该看 True / 该藏 False.
    """
    if role == _ROLE_USER:
        # 用户真说的话 —— 永远可见. 注意这不等于「wire 里 role=user 的都可信」:
        # 模型自己编出来的「用户说...」在 wire 里的角色是 assistant, 走不到这一支.
        return True
    if role == _ROLE_ASSISTANT:
        # 带工具调用的那一轮是过程叙述 (「让我先查一下」), 不是答案;
        # 不带工具调用的才是在回答.
        return not tool_call_ids
    # tool (工具回填) 与 system (续写指令 / 摘要 / 角色设定) 都是内部件.
    return False


def _tool_call_ids(message: ModelMessage) -> list[str]:
    """从一条 wire 消息里取出它发起的工具调用编号 (取不到就是空列表).

    wire 的形状是嵌套的: `[{"id": "call_0", "type": "function",
    "function": {"name": ..., "arguments": ...}}]`. 这里只关心 id —— 工具名与
    参数由 ToolCall 那一行单独存.

    宽容处理是有意的: 历史可能是手工拼的, 一条缺 id 的坏数据不该让整段会话读
    不出来 (跳过它即可).
    """
    raw_calls = message.get(_TOOL_CALLS)
    if not isinstance(raw_calls, list):
        return []
    ids: list[str] = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            continue
        call_id = raw.get("id")
        if isinstance(call_id, str) and call_id:
            ids.append(call_id)
    return ids


def assistant_answer(
    content: str | None, messages: Sequence[ModelMessage]
) -> tuple[str | None, str | None]:
    """算出「该交给用户的答复正文」与「这条答复的思维链」.

    Args:
        content: `LoopResult.content` —— 框架拼好的权威答复. **必须传它**,
            不要自己从 messages 数组末尾取 (截断续写时末尾只是尾段).
        messages: 本轮新增的 wire 消息 (与 visible_transcript 用同一段切片).

    Returns:
        tuple[str | None, str | None]: (答复正文, 思维链).
        - 正文: 原样返回 content; 它是 None (没答出正文) 时也返回 None ——
          会话历史里这时的 assistant 行 content 为空, 前端按「这次没答出来」渲染.
        - 思维链: 把这段里所有 assistant 消息的 reasoning_content 依序拼起来
          (续写的每一段各有各的思考过程, 只留最后一段会丢前面的).
    """
    parts = [
        reasoning
        for reasoning in (_reasoning_of(message) for message in messages)
        if reasoning
    ]
    return content, ("\n".join(parts) if parts else None)


def _reasoning_of(message: ModelMessage) -> str | None:
    """取一条消息的思维链 (不是字符串或为空则 None)."""
    if not isinstance(message, dict):
        return None
    raw = message.get(_REASONING)
    return raw if isinstance(raw, str) and raw else None


def conversation_turns(
    messages: Sequence[ModelMessage],
    content: str | None,
    *,
    since: int = 0,
) -> list[TurnPair]:
    """把一段 wire 历史收成「一问一答」列表 (落库与前端展示的同一条口径).

    Args:
        messages: 完整 wire 消息历史 (`LoopResult.messages`).
        content: `LoopResult.content` —— 框架拼好的最终答复 (见 assistant_answer).
        since: 只从第几条开始收 (0 = 从头). 调用方通常是「下一轮 run」, 它知道
            跑之前历史有多长, 传进来就只收新增的那一段, 不会重复写老消息.

    Returns:
        list[TurnPair]: 按时间排好的一问一答. 常见就是一条 (一次 run 一般是
        一问一答); 但历史里若有多条用户消息 (调用方一次喂了多轮), 会全部收进来,
        只有**最后一条**带上最终答复 —— 前面几问的答案在它们各自的 messages 里,
        不在本次的 content 里, 硬套会张冠李戴.

    边界: 一段历史里一条用户消息都没有 (纯系统触发 / 工具续跑) 就返回空列表 ——
    没有「问」就谈不上「答」, 这样的内容不进会话历史 (它在快照里, 归 transcript).
    """
    if since < 0:
        since = 0
    picked = list(messages[since:]) if since else list(messages)
    questions = _question_indexes(picked)
    if not questions:
        return []

    # 思维链只挂到最后一条答复上: 续写的各段都是**同一次回答**的组成部分,
    # 前面几问的思维链不属这一段 (它们在各自的 content 与快照里).
    answer, reasoning = assistant_answer(content, picked[questions[-1] :])

    return [
        TurnPair(
            question=_text_of(picked[index]),
            answer=answer if position == len(questions) - 1 else None,
            reasoning=reasoning if position == len(questions) - 1 else None,
        )
        for position, index in enumerate(questions)
    ]


def _question_indexes(messages: Sequence[ModelMessage]) -> list[int]:
    """找出里面所有「用户真说的」消息的下标 (判定规则与 _is_visible 同源)."""
    return [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict) and message.get(_ROLE) == _ROLE_USER
    ]


def _text_of(message: Any) -> str:
    """取一条消息的正文文本 (不是字符串则空串 —— 用户消息理论上必有正文)."""
    raw = message.get(_CONTENT) if isinstance(message, dict) else None
    return raw if isinstance(raw, str) else ""


def count_visible(lines: Sequence[TranscriptLine]) -> int:
    """数一数这些消息里有几条是给前端看的 (前端分页与「这个会话聊了几轮」用)."""
    return sum(1 for line in lines if line.visible)
