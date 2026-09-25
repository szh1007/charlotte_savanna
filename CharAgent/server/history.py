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

**未决挂起也在这里面** (issue 34): 响应多一个字段 `pending_approval`, 有未决挂起
时是那一次调用 (含重建确认卡要的四样: 哪一条 / 哪个工具 / 问什么 / 缺什么), 没有
时是 None. **不新建表、不新建端点**: 挂起态的宿主本来就是
`charagent_tool_calls` 那一条 (`status = needs_approval AND approved_at IS NULL`,
ADR-0014), 这里只是把它读出来交给前端.

为什么这条字段必须在历史接口上 (而不是只靠事件流): 前端读的是**记录**(transcript),
而挂起态在工具调用表 —— 两条读取路径. 少了它, 刷新页面之后那张确认卡就消失, 而
用户**永远没法完成那次代付** (卡没了, 也就没有地方点确认). 它是「刷新恢复」的全部
实现.

**只读**: 本模块一行都不碰会话状态, 有副作用的那条路是 `POST /runs`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from CharAgent.db.entities import Message, ToolCall
from CharAgent.model.utils.types import ModelMessage

# 只读历史的路由 (与 POST /runs 并肩: 同一道门认身份, 一个跑一个看)
HISTORY_PATH = "/history"

# 响应体的字段: 这段对话是谁的 + 它的对话正文 + 有没有等着人确认的挂起
THREAD_ID_FIELD = "thread_id"
MESSAGES_FIELD = "messages"
PENDING_APPROVAL_FIELD = "pending_approval"

# 未决挂起那一块里的四个键 —— 与 `approval_required` 事件的载荷同源 (前端一套
# 渲染逻辑吃两边: 事件流里来的与刷新之后重建的, 长得一样)
APPROVAL_RUN_ID_FIELD = "run_id"
APPROVAL_TOOL_CALL_ID_FIELD = "tool_call_id"
APPROVAL_TOOL_NAME_FIELD = "tool_name"
APPROVAL_PROMPT_FIELD = "prompt"
APPROVAL_NEEDS_FIELD = "needs"

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


def pending_approval_row(rows: Iterable[ToolCall]) -> dict[str, Any] | None:
    """未决挂起的那些行 → 交给前端的**一块**, 或者 None (没有挂着的东西).

    交出去的是**能重建一张确认卡**的最小集 (白名单, 与 `conversation_messages`
    同一条纪律):

    | 键 | 给谁用 |
    |---|---|
    | `run_id` | 前端 POST 恢复时指名道姓要的那一次运行 |
    | `tool_call_id` / `tool_name` | 把卡片插在会话流里那一次调用的位置上 |
    | `prompt` | 卡片上那句**给用户看的话** (业务给的, 框架原样转) |
    | `needs` | 机器可读的缺失项: 含 `payment_password` 就渲染一个密码框, |
    | | 空就只给两个按钮 |

    **只取第一条**: 框架一次只挂一条 (`agent/loop.py` 的「一次挂起只挂一条」),
    所以正常情形下这里就只有一条; 真出现多条时, 交出去的那条是**最早的**那个
    (与 `ToolCallsRepository.list_pending_approvals` 的排序一致), 而不是最后一条
    —— 用户欠的账按先来后到还.

    Args:
        rows: `list_pending_approvals` 的产出 (已经按会话与「未决」筛过).

    Returns:
        dict[str, Any] | None: 那一块; 没有未决挂起时 None (前端按空处理).
    """
    for row in rows:
        return {
            APPROVAL_RUN_ID_FIELD: row.run_id,
            APPROVAL_TOOL_CALL_ID_FIELD: row.tool_call_id,
            APPROVAL_TOOL_NAME_FIELD: row.tool_name,
            APPROVAL_PROMPT_FIELD: row.approval_prompt,
            APPROVAL_NEEDS_FIELD: list(row.approval_needs or ()),
        }
    return None


__all__ = [
    "APPROVAL_NEEDS_FIELD",
    "APPROVAL_PROMPT_FIELD",
    "APPROVAL_RUN_ID_FIELD",
    "APPROVAL_TOOL_CALL_ID_FIELD",
    "APPROVAL_TOOL_NAME_FIELD",
    "DISPLAY_ROLES",
    "HISTORY_PATH",
    "MESSAGES_FIELD",
    "PENDING_APPROVAL_FIELD",
    "THREAD_ID_FIELD",
    "conversation_messages",
    "conversation_of",
    "pending_approval_row",
]
