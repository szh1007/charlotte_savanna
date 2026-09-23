"""会话列表的只读视图: 记录表里的会话行 → 前端左侧那一栏要的东西.

一句话理解: 「我有哪些对话」这个问题的答案. 与 `history.py` 是同一对关系 ——
那个回答「这段对话聊了什么」, 这个回答「我聊过哪几段」.

**它读的还是记录表那条线**: 会话行来自 `charagent_threads` (`ThreadsRepository`),
「聊过话」的判据是**有一条可见消息** —— 全是空壳与只剩内部件的会话都不进列表
(勾了「新建」还没开口的那一段, 点进去是一片空白, 列出来只是噪音).

**四个字段各自回答一件事** (少一个前端就得猜):

| 字段 | 回答 |
|------|------|
| `conversation_id` | **点进去时该带什么** —— 请求头 `X-Conversation-Id` 收的就是它 |
| `title` | 列表上显示什么 (首条用户消息, 建会话时定的; 用户改过就是用户起的) |
| `updated_at` | 排在哪一行 (置顶的除外, 其余按它倒序 —— 刚聊过的在最前) |
| `pinned_at` | 那颗菜单里显示「置顶」还是「取消置顶」; null = 没置顶 |

**同一段会话上还有三个写动作** (#20): 改标题 / 置顶 / 删除, 三条路由都挂在
`/conversations` 下面 (`/conversations/title` 等), 会话编号一律走
`X-Conversation-Id` 头 —— 与 `/history` 同一条纪律. 三者的归属判据不在这一层:
仓储那三个写方法把 `(tenant_id, user_id)` 写进了 WHERE, 一行都没命中就是
`ThreadNotFoundError` (404).

**`conversation_id` 为什么是会话编号的第三段**: 会话编号是
`业务:用户ID:对话ID` (PRD §4.11), 前两段说的是「这是谁的东西」, 而列表本来就是
按属主查的 —— 于是一条记录里**能变的只有第三段**. 前端拿着它去开一段对话, 服务端
把它接回完整编号, 两边的指代只有一个值. 这是框架**唯一一次**读会话编号的结构
(业务自己拼的格式, 框架平时只当它是个不透明的主键), 所以那两行解析留在这里,
不进 db 层.

**分页只给 `limit`**: 仓储的既有形状 (没有 offset), 前端要「更多」就自己带一个
更大的数 —— 这一层不发明游标.
"""

from __future__ import annotations

from typing import Any

from CharAgent.db.entities import Thread

# 只读会话列表的路由 (与 POST /runs / GET /history 并肩)
CONVERSATIONS_PATH = "/conversations"
# 三个管理动作 (#20): 都打在同一段会话上, 会话编号走 `X-Conversation-Id` 头 ——
# 与 `/history` 同一条纪律 (编号不进 URL, 访问日志里就不留痕).
TITLE_PATH = "/conversations/title"
DELETE_PATH = "/conversations/delete"
PIN_PATH = "/conversations/pin"

# 响应体: 一把会话 + 这次给了几条 (调用方按 limit 判断要不要再要)
CONVERSATIONS_FIELD = "conversations"
LIMIT_QUERY = "limit"
# 搜索: 列表端点的一个**可选**过滤条件, 不是另一条路由 (理由见仓储的模块 docstring)
QUERY_QUERY = "q"

# 每条记录的四个字段
CONVERSATION_ID_FIELD = "conversation_id"
TITLE_FIELD = "title"
UPDATED_AT_FIELD = "updated_at"
# 置顶时刻 (ISO 8601); null = 没置顶. 前端据它决定那颗菜单里显示「置顶」还是
# 「取消置顶」—— 少这一个字段, 页面就只能靠猜或者再问一次
PINNED_AT_FIELD = "pinned_at"

# 三个管理动作各自认的请求体字段
PINNED_FIELD = "pinned"
DELETED_FIELD = "deleted"

# 改标题时卡的那条线 (字符数).
#
# 比 `recorder.TITLE_LIMIT` (60) 宽: 那个是**自动标题**的截断长度 (取自首条用户
# 消息), 而用户自己起名该有一点余地. 比标题列 (`String(255)`) 窄得多: 库那一条
# 是最后一道闸, 不该等撞到它才说「太长」. 列表上那一行只放得下十几个字, 一百个
# 字符已经足够写清楚「这段对话是干什么的」.
MAX_TITLE_LENGTH = 100

# 会话编号的分段符 (PRD §4.11 的格式; 框架只在这一处依赖它的形状)
THREAD_ID_SEPARATOR = ":"


def conversation_id_of(thread_id: str) -> str:
    """完整会话编号 → 第三段 (对话 ID).

    规格管它叫「第三段」, 而这里写的是**最后**一段 —— 编号恰好三段时两者等价
    (本业务就是三段), 之所以不写死下标, 是因为前两段 (业务 / 属主) 由业务拼,
    它们里面有没有分隔符框架管不着; 唯一能确定的是对话 ID 在最后 (它正是**业务
    每次新开一段对话时换的那个值**).

    没有分隔符时原样返回: 那种编号不是本约定拼出来的 (测试里的玩具业务就是),
    返回它本身比返回空串更有用.

    Args:
        thread_id: 完整会话编号 (如 `shop:3:web`).

    Returns:
        str: 最后一段 (如 `web`).
    """
    return thread_id.rsplit(THREAD_ID_SEPARATOR, 1)[-1]


def conversation_row(thread: Thread) -> dict[str, Any]:
    """会话实体 → 交出去的一行 (四个字段, 一个都不多给).

    `updated_at` 交给 ISO 8601 文本: 响应体是 JSON, 而「按它倒序」的语义要求前端
    能比较 —— 时间字符串按字典序比就是按时间比 (同一时区同一格式), 前端不必为了
    排序去解析时区.

    `pinned_at` 同一个形状 (ISO 文本或 null), 于是前端拿 `Boolean(row.pinned_at)`
    就知道该显示「置顶」还是「取消置顶」.
    """
    return {
        CONVERSATION_ID_FIELD: conversation_id_of(thread.thread_id),
        TITLE_FIELD: thread.title,
        UPDATED_AT_FIELD: thread.updated_at.isoformat(),
        PINNED_AT_FIELD: (
            thread.pinned_at.isoformat() if thread.pinned_at is not None else None
        ),
    }


__all__ = [
    "CONVERSATIONS_FIELD",
    "CONVERSATIONS_PATH",
    "CONVERSATION_ID_FIELD",
    "DELETED_FIELD",
    "DELETE_PATH",
    "LIMIT_QUERY",
    "MAX_TITLE_LENGTH",
    "PINNED_AT_FIELD",
    "PINNED_FIELD",
    "PIN_PATH",
    "QUERY_QUERY",
    "THREAD_ID_SEPARATOR",
    "TITLE_FIELD",
    "TITLE_PATH",
    "UPDATED_AT_FIELD",
    "conversation_id_of",
    "conversation_row",
]
