"""会话历史的只读视图: wire 历史 → 能给人看的那一份对话.

一句话理解: 会话按 `thread_id` 长驻在登记表里 (`sessions.SessionRegistry`),
浏览器刷新之后要把聊过的话拿回来 —— 本模块负责**取哪几段**与**取哪几个字段**.

**为什么不能把 wire 历史原样交出去**: 那份历史是发给模型的东西, 三类角色里只有
两类是「对话」:

| role | 是什么 | 为什么不出去 |
|------|--------|-------------|
| `system` | 业务写的身份说明 | 那是业务对模型说的话, 不是对用户说的话 |
| `tool` | 工具返回的正文 | 单号 / 地址 / 余额就在里面 |
| `assistant` | 模型的答复 (可能带 `tool_calls`) | 只留 `content`: 另两个是内部痕迹 |

于是本模块**逐条新建** `{"role", "content"}` 两个键, 一个字段都不复制 —— 白名单,
不是黑名单: wire 消息以后加了新字段, 默认也进不了浏览器, 要放行得回来改这里.

**只读**: 本模块一行都不碰会话状态 (它只读 `ChatSession.history`, 而那个属性给的
是浅拷贝). 有副作用的那条路是 `POST /runs`, 不是这里.
"""

from __future__ import annotations

from collections.abc import Iterable

from CharAgent.model.utils.types import ModelMessage

# 只读历史的路由 (与 POST /runs 并肩: 同一道门认身份, 一个跑一个看)
HISTORY_PATH = "/history"

# 响应体的两个字段: 这段对话是谁的 + 它的对话正文
THREAD_ID_FIELD = "thread_id"
MESSAGES_FIELD = "messages"

# 交出去的两种角色 (上表里能给人看的那两类)
DISPLAY_ROLES = frozenset({"user", "assistant"})


def conversation_of(messages: Iterable[ModelMessage]) -> list[dict[str, str]]:
    """wire 历史 → 展示用的对话 (只留 user / assistant 的非空 `content`).

    Args:
        messages: 会话的 wire 消息 (通常是 `ChatSession.history`).

    Returns:
        list[dict[str, str]]: 逐条 `{"role", "content"}`; 被滤掉的角色与空内容
        直接不出现 (顺序不变).

    Note:
        中间轮的 assistant 消息 (带 `tool_calls` 的那种「我先查一下」) 会**照样
        出现**: 它是模型当时真说过的话, 而调用方按 role 顺序渲染时, 同一问之下
        的几段答复连起来读就是完整回答. 想只留最后一段的调用方自己按需裁 ——
        框架不替它猜「哪一段才算答复」.
    """
    conversation: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in DISPLAY_ROLES:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        conversation.append({"role": role, "content": content})
    return conversation


__all__ = [
    "DISPLAY_ROLES",
    "HISTORY_PATH",
    "MESSAGES_FIELD",
    "THREAD_ID_FIELD",
    "conversation_of",
]
