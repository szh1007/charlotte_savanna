"""会话历史的只读视图: 记录表的行 → 能给人看的那一份对话.

一句话理解: 浏览器刷新之后要把聊过的话拿回来 —— 本模块负责**取哪些行、交哪些
字段**, 以及为什么是它说了算.

**取数来源 (ticket 17 起换成记录表)**: `GET /history` 读的是**记录表**
(`charagent_messages`, 见 db/recorder.py), 不再是进程内存里那份会话历史. 理由是
两者要回答的问题不同:

| 来源 | 是什么 | 刷新之后 |
|------|--------|---------|
| 会话内存 (`ChatSession.history`) | 这一轮进程里发给模型的东西 | 进程一换就没了 |
| 快照 (`charagent_checkpoints`) | 「接着跑」用的断点 | 会被压缩改写成摘要视图 |
| **记录表** | **给人看的那份持久记录** | **重启之后照样在** |

于是「用户看到的记录永不压缩」这句话有了落点: 同一条对话刷新两次、隔一次重启
再刷新, 看到的都是同一份.

**两个来源只按配置二选一, 不按成败兜底**: 装配时给了库 (与 `database=`) 就读
记录表, 没给就读会话内存 —— 但**绝不**在「记录表读不到」时退回内存. 两条来源
的过滤规则不同 (记录表按 `hidden`, 内存按角色), 一处退回一处不退回, 会让同一段
对话刷新两次看到不一样的东西 —— 那是最难查的一类 bug.

**两个来源各有一个投影函数** (`conversation_messages` / `conversation_of`):
过滤规则确实不同 (一个按 `hidden`, 一个按角色), 所以不硬凑成一个 —— 但也**只有
这两个**, 装配时选定一个就一直用它.

**为什么两个字段**: 记录表的行有十几个字段 (编号 / 归属 / 思维链 / 工具调用编号
/ hidden...), 而这里**逐条新建** `{"role", "content"}` 两个键, 一个都不复制 ——
白名单, 不是黑名单: 实体以后加了新字段, 默认也进不了浏览器, 要放行得回来改这里.
`content` 可能是 null (assistant 那一行没吐出正文), 那是**如实**: 前端按「这次没
答出来」渲染, 比什么都不给诚实.

**角色为什么不过滤**: 老实现只放行 `user` / `assistant`, 而记录表里那两条可见的
`system` 说明 (「这一轮没答完」/「这中间有一轮没能记录下来」) 正是要给用户看的 ——
「哪些该给人看」这件事已经由 `hidden` 列回答过了 (写入时定的, 见
db/conversation.py), 这里再筛一次角色等于把那条说明吞掉.

**只读**: 本模块一行都不碰会话状态, 有副作用的那条路是 `POST /runs`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from CharAgent.db.entities import Message
from CharAgent.model.utils.types import ModelMessage

# 只读历史的路由 (与 POST /runs 并肩: 同一道门认身份, 一个跑一个看)
HISTORY_PATH = "/history"

# 响应体的两个字段: 这段对话是谁的 + 它的对话正文
THREAD_ID_FIELD = "thread_id"
MESSAGES_FIELD = "messages"

# 内存那份来源交出去的两种角色 (记录表那份不用它 —— `hidden` 列已经筛过了).
# 老实现留下的白名单: 会话历史里 system 是业务对模型说的话, tool 是工具正文
# (单号 / 地址 / 余额都在里面), 两类都不该原样给用户.
DISPLAY_ROLES = frozenset({"user", "assistant"})


def conversation_of(messages: Iterable[ModelMessage]) -> list[dict[str, str]]:
    """**内存那份**历史 → 展示用的对话 (只留 user / assistant 的非空正文).

    与 `conversation_messages` 是同一件事在两个来源上的做法, 差别只有过滤规则:
    这里按**角色**挑 (wire 历史里没有 hidden 标记), 那边按 `hidden` 挑.

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


def conversation_messages(rows: Iterable[Message]) -> list[dict[str, Any]]:
    """记录表的行 → 交出去的对话 (只留 `role` 与 `content` 两个键).

    Args:
        rows: `MessagesRepository.list_conversation` 的产出 (已经按 `hidden`
            过滤过, 早的在前).

    Returns:
        list[dict[str, Any]]: 逐条 `{"role", "content"}`; `content` 为 None 的
        行照样出现 (那是「问了没答出来」, 不是「没发生」).
    """
    return [{"role": row.role, "content": row.content} for row in rows]


__all__ = [
    "DISPLAY_ROLES",
    "HISTORY_PATH",
    "MESSAGES_FIELD",
    "THREAD_ID_FIELD",
    "conversation_messages",
    "conversation_of",
]
