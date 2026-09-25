"""消息 (Message) 的存取: 写会话消息、读会话历史、读完整 transcript.

一句话理解: 这是**消息柜员**, 而它最重要的本事是**分得清两种读法**:

- `list_conversation` —— **给前端看的一问一答** (只取 `hidden = False` 的那些).
  这就是会话分层要的那条线: `GET /threads/{id}/messages` 走它.
- `list_transcript` —— **完整记录** (含隐藏的内部件). 给排查与审计用, 不喂前端.

分层的规则不在这里 —— 在 `db/conversation.py`. 本仓储只负责「按 hidden 取
哪些行」, 不管「哪条该 hidden」. 这样分层规则改了只动一个文件, 而读法 (取可见
还是取全部) 是接口层面的事, 两者不混.

**写入的两种口径** (同一个分层在写侧的体现):
- `add_turn`: 写「一问一答」两行 (都是可见的) —— 面向会话历史的常规写法.
- `add_lines`: 写一段 transcript 的逐条 (自动带上 hidden 标记) —— 需要留全量
  记录时用. 完整 wire 历史归 Checkpoint, 所以**默认不写**它,
  除非调用方明确要留着排查.

**编号可推导** (ticket 27 起): `add_lines` 可以收一串**显式编号** (由
`message_id_for` 从「哪次运行 + 第几条」算出来). 记录层从那时起是「产生即落库 +
收尾补齐」—— 同一条消息会被写两次, 而工具调用行的外键指着它, 两次必须落在
**同一行**上. 于是 id 不再是插入时随机生成的, 而是**算得出来的**; 重复写同一批
行是幂等的 (已有该编号就跳过), 修订走 `update_line`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from CharAgent.db.conversation import TranscriptLine, TurnPair
from CharAgent.db.entities import Message, MessageRole
from CharAgent.db.errors import DataConfigError
from CharAgent.db.repositories.base import PgRepository
from CharAgent.db.schema import messages


def message_id_for(run_id: str, index: int) -> str:
    """由「哪次运行 + 第几条」派生的消息编号 (同一次运行里唯一的身份).

    为什么不用随机 id (2026-09-24, ticket 27): 记录层从「收尾一次性写」改成
    「产生即落库」之后, 同一条消息会写两次 (运行中那一拍 + 收尾补齐那一拍), 而
    `charagent_tool_calls.message_id` 是指向它的**外键** —— 两次必须落在同一行上.
    派生键是 `(run_id, 本次 run wire 历史里的下标)`: 运行内唯一 (历史只增不改),
    跨运行天然分开 (run_id 是 uuid4 hex), 而**同一次运行的第二段** (审批恢复)
    因此能直接算出第一段写下那一行的编号, 不必反查.

    Args:
        run_id: 哪次运行 (`charagent_runs.run_id`).
        index: 这条消息在本次 run wire 历史里的下标 (早的在前, 从 0 起).
    """
    return f"{run_id}:{index}"


class MessagesRepository(PgRepository):
    """消息表的读写口."""

    async def add_turn(
        self,
        *,
        thread_id: str,
        turn: TurnPair,
        run_id: str | None = None,
        noticed_at: datetime | None = None,
    ) -> list[Message]:
        """把一条「一问一答」写成两行 (user 行 + assistant 行, 都是可见的).

        这是会话历史的常规写法: 上层从 `LoopResult` 收出一问一答 (见
        `conversation.conversation_turns`), 交给这里落库.

        Args:
            thread_id: 属于哪段会话.
            turn: 一问一答的内容 (问题 + 答复正文 + 思维链).
            run_id: 是哪次运行产生的; None 表示不是 agent 答的 (人工客服回复).
            noticed_at: 显式指定「这一问发生的时刻」(测试用); None 则取当下.

        Returns:
            list[Message]: 写进去的两行 (先 user 后 assistant, 顺序与时间一致).
            答复正文为 None 时也会写那一行 —— 前端据此渲染「这次没答出来」,
            比「什么都没有」诚实.

        Raises:
            DataStoreError: 写库失败 (会话不存在 / 库连不上).
        """
        moment = noticed_at if noticed_at is not None else datetime.now(UTC)
        rows = [
            Message(
                message_id=uuid4().hex,
                thread_id=thread_id,
                run_id=run_id,
                role=MessageRole.USER.value,
                content=turn.question,
                reasoning=None,
                tool_call_ids=[],
                hidden=False,
                created_at=moment,
            ),
            Message(
                message_id=uuid4().hex,
                thread_id=thread_id,
                run_id=run_id,
                role=MessageRole.ASSISTANT.value,
                content=turn.answer,
                reasoning=turn.reasoning,
                tool_call_ids=[],
                hidden=False,
                # 比问题晚 1 微秒: 两条同一时刻会靠主键排序, 顺序就成了随机的;
                # 差一点时间才能保证「问在前、答在后」稳定可复现
                created_at=moment + timedelta(microseconds=1),
            ),
        ]
        await self.add_messages(rows)
        return rows

    async def add_messages(
        self, rows: Sequence[Message], *, skip_existing: bool = False
    ) -> None:
        """批量写消息 (行已由调用方造好, 这里只负责落库).

        比 `add_turn` 低一层: 需要自己控制每一行的 role / hidden 时用它 (比如
        写一段 transcript). 要求这批行的 thread_id 一致 —— 会话是分区键,
        混着写几乎总是调用方把两个会话的数据串了, 早点报出来.

        Args:
            rows: 要落库的行.
            skip_existing: True 表示编号已经在了就跳过 (`ON CONFLICT DO NOTHING`)
                —— 「同一批行会被写两次」的调用方要它 (记录层的产生即落库 + 收尾
                补齐, 见 `add_lines`); False (默认) 是老行为: 撞上唯一约束就报错.

        Raises:
            DataStoreError: 写库失败.
            DataConfigError: 这批行跨了多个会话.
        """
        if not rows:
            return
        thread_ids = {row.thread_id for row in rows}
        if len(thread_ids) > 1:
            raise DataConfigError(
                "同一批消息只能属于一个会话, 实际跨了 "
                f"{len(thread_ids)} 个: {sorted(thread_ids)}"
            )
        statement = messages.insert()
        if skip_existing:
            # ON CONFLICT DO NOTHING 是 Postgres 的方言写法 (与
            # checkpoint/postgres.py 同一个 import): 本层的库就是它
            statement = pg_insert(messages).on_conflict_do_nothing()
        async with self._session() as session:
            session.execute(statement, [self._params(row) for row in rows])

    async def add_lines(
        self,
        *,
        thread_id: str,
        lines: Sequence[TranscriptLine],
        run_id: str | None = None,
        noticed_at: datetime | None = None,
        ids: Sequence[str] | None = None,
    ) -> list[Message]:
        """把一段 transcript 逐条落库 (**保留 hidden 标记**, 只读接口才用得到).

        为什么要有它 (而不是只留 add_turn): 「完整 wire 历史归 Checkpoint」是
        既定分层, 但排查线上问题时, 库里直接有一份带标记的记录比翻快照方便.
        于是留一个**显式**入口: 调用方明确要留全量时才写, 不会不知不觉把内部件
        写进会话历史.

        **这个入口是幂等的** (ticket 27 起): 同一个编号已经写过了就跳过
        (`ON CONFLICT DO NOTHING`). 记录层要「产生即落库 + 收尾补齐」两次写同一批
        行, 靠的就是它 —— 而**已存在的那一行不会被这次的内容覆盖**: 收尾要把某几行
        改成别的样子 (截断续写合成一条) 得走 `update_line`, 那是另一件事.

        Args:
            thread_id: 属于哪段会话.
            lines: `conversation.visible_transcript(...)` 的产出 (带 hidden 标记).
            run_id: 是哪次运行产生的.
            noticed_at: 起始时刻; None 则取当下. 逐条加 1 微秒保证顺序稳定.
            ids: 逐条的编号 (与 lines 一一对应), 通常由 `message_id_for` 算出来;
                None 表示每条随机生成一个 (写第二遍就会多出重复行 —— 只有「这一批
                不会被写第二次」的调用方可以不给).

        Returns:
            list[Message]: 这一批行 (含**上一次已经写过的**那些: 编号是派生的, 两次
            写的是同一行, 交回整批比只交新插的更如实).

        Raises:
            DataConfigError: ids 的长度与 lines 对不上 (那是调用方算错了下标).
        """
        if ids is not None and len(ids) != len(lines):
            raise DataConfigError(
                f"显式编号必须与行一一对应, 实际 {len(ids)} 个编号 / {len(lines)} 行"
            )
        base = noticed_at if noticed_at is not None else datetime.now(UTC)
        rows = [
            Message(
                message_id=ids[offset] if ids is not None else uuid4().hex,
                thread_id=thread_id,
                run_id=run_id,
                role=line.role,
                content=line.content,
                reasoning=line.reasoning,
                tool_call_ids=list(line.tool_call_ids),
                hidden=line.hidden,
                created_at=base + timedelta(microseconds=offset),
            )
            for offset, line in enumerate(lines)
        ]
        if rows:
            await self.add_messages(rows, skip_existing=True)
        return rows

    async def update_line(
        self,
        message_id: str,
        *,
        content: str | None,
        reasoning: str | None,
        hidden: bool,
    ) -> bool:
        """改一条**已经落库**的行的正文 / 思维链 / 可见性 (收尾**修订**用).

        为什么要能改: 记录层从 ticket 27 起是「产生即落库」—— 运行中写下的那几条是
        **当时的形态** (截断续写的每一段各自是可见的 assistant 行), 而收尾才知道这
        一轮答复的**最终形态** (几段合成一条, 早先那几段退成隐藏行; 规则见
        `conversation.recorded_transcript`). 修订只动这三列: 编号 / 归属 / 时刻都是
        产生那一刻的事实, 不该被改写.

        Args:
            message_id: 改哪一行.
            content / reasoning / hidden: 这一行**最终**该长什么样 (不传 None 表示
                「就是没有正文 / 思维链」, 与「不动这一列」不是一回事 —— 修订是设置
                目标形态, 不是打补丁).

        Returns:
            bool: 改到了 True; False = 没有这个编号 (运行中被写失败过).
        """
        statement = (
            update(messages)
            .where(messages.c.message_id == message_id)
            .values(content=content, reasoning=reasoning, hidden=hidden)
        )
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    async def list_conversation(
        self, thread_id: str, *, limit: int | None = None
    ) -> list[Message]:
        """取**给前端看的**会话历史 (只含 `hidden = False` 的行), 早的在前.

        这是 `GET /threads/{id}/messages` 的实现 —— 断线重连与人工接管都靠它.

        Args:
            thread_id: 哪段会话.
            limit: 最多取**最近**几条 (None = 全部); 取回来仍按时间正序.
        """
        return await self._list(thread_id, visible_only=True, limit=limit)

    async def list_transcript(
        self, thread_id: str, *, limit: int | None = None
    ) -> list[Message]:
        """取**完整记录** (含隐藏的内部件), 早的在前 —— 排查与审计用, 不喂前端.

        与 `list_conversation` 的差别只有一条: 不按 hidden 过滤. 分成两个方法而
        不是一个方法的参数, 是为了让调用处一眼看出「我这次要的是哪种」——
        `list_transcript(thread_id)` 出现在接口层就是明摆着的错用.
        """
        return await self._list(thread_id, visible_only=False, limit=limit)

    async def conversation_pairs(
        self, thread_id: str, *, limit: int | None = None
    ) -> list[TurnPair]:
        """把会话历史收成「一问一答」列表 (接口直接返回的形态).

        消息表里一行一条 (问一行、答一行), 而前端要的是成对的; 这里顺手配好,
        免得每个调用方各写一遍配对逻辑. 配对规则: 按时间顺序, 遇到一条 user
        消息就记下问题, 紧接着的 assistant 消息就是它的答案.

        边界: 末尾只有问题没有答案时, 那条问题的 answer 是 None —— 如实反映
        「问了还没答」, 不吞掉.
        """
        rows = await self.list_conversation(thread_id, limit=limit)
        pairs: list[TurnPair] = []
        pending: str | None = None
        for row in rows:
            if row.role == MessageRole.USER.value:
                if pending is not None:
                    # 上一条问题还没等到答案就又来了一问 (人工介入等场景):
                    # 如实收成一条「没答出来」的记录, 不静默丢掉
                    pairs.append(TurnPair(question=pending, answer=None))
                pending = row.content or ""
            elif row.role == MessageRole.ASSISTANT.value and pending is not None:
                pairs.append(
                    TurnPair(
                        question=pending,
                        answer=row.content,
                        reasoning=row.reasoning,
                    )
                )
                pending = None
        if pending is not None:
            pairs.append(TurnPair(question=pending, answer=None))
        return pairs

    async def _list(
        self, thread_id: str, *, visible_only: bool, limit: int | None
    ) -> list[Message]:
        """两种读法的共同实现 (差别只在要不要按 hidden 过滤).

        「最近 N 条」在库里倒着取最快, 取回来再翻正 (返回顺序仍是早 -> 晚).
        """
        if limit is not None and limit <= 0:
            return []
        statement = select(Message).where(messages.c.thread_id == thread_id)
        if visible_only:
            statement = statement.where(messages.c.hidden.is_(False))
        if limit is None:
            statement = statement.order_by(
                messages.c.created_at.asc(), messages.c.message_id.asc()
            )
        else:
            statement = statement.order_by(
                messages.c.created_at.desc(), messages.c.message_id.desc()
            ).limit(limit)
        async with self._session() as session:
            rows = list(session.scalars(statement))
        return rows if limit is None else list(reversed(rows))

    @staticmethod
    def _params(row: Message) -> dict[str, object]:
        """实体 → 插入参数."""
        return {
            "message_id": row.message_id,
            "thread_id": row.thread_id,
            "run_id": row.run_id,
            "role": row.role,
            "content": row.content,
            "reasoning": row.reasoning,
            "tool_call_ids": row.tool_call_ids,
            "hidden": row.hidden,
            "created_at": row.created_at,
        }
