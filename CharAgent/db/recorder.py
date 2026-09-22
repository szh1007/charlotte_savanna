"""会话记录: 每次运行收尾把这一轮投影进 `charagent_messages` / `runs` (ticket 17).

一句话理解: 这是**记录员**. 一次问答跑完, 他把「用户问了什么、模型答了什么、
中间调过什么工具」写成几行落进库; 下一次谁要看这段对话 (前端刷新、换台设备、
排查线上问题), 看到的都是他写下的那份.

**它为什么必须存在** (而不是继续让会话内存当唯一真相): 会话活在进程内存里
(`client/session.py` 的 `_history`), 而快照是**给「接着跑」用的** —— 它是机器还原
现场要的形态 (编号 + 全量账本 + 计数器), 且 Redis 那份会过期. 注意压缩**不在**
快照这一侧: 压缩只改「这一次请求送给模型什么」, 帧里存的永远是全量账本 (见
`agent/loop.py` 的 `_record_turn`). 记录表是另一件事: 它是**给人看的那份持久记录**,
只增不减、永不压缩 (PLAN 的 L2.5 一句话: 「用户看到的记录永不压缩」). 两者同源
(都从 `LoopResult` 来) 而用途不同.

**什么时候写**: 每次运行**收尾写一次** (不是每轮) —— 一轮 = 用户点一次「发送」到
agent 答完, 这正好是「一句话的账」. 每轮写一次会让一次带工具的问答写出十几行
半成品, 而半成品正是这个记录最不该有的东西.

**写什么** (三层都由 `db/conversation.py` 的规则算好, 本模块不另判一遍):

| 落库的 | 内容 |
|--------|------|
| `runs` 一行 | 这次运行的结局 (`RUN_STATUS_FOR_OUTCOME` 映射的终态) 与轮次 / |
| | 用量 —— 用量含**分解** (输入 / 输出 / 思维链 / 缓存命中 / 未命中) 与 |
| | 提示词版本, 见 `RunFacts` |
| 可见消息 | 用户那句提问 + 最终答复正文 (**取 `LoopResult.content`**,
| | 理由见 conversation.py 那条硬规矩) |
| 隐藏消息 | 工具回填、带工具调用的中间轮、续写指令、**新压出来的摘要** |
| | 都是 `hidden=True` —— 留着供审计, 不推给前端 |

**会话行懒创建**: 第一次写某段会话时按 `thread_id` 取, 没有就用首条用户消息当
标题建一个. 于是**不需要**单独的「创建会话」端点 —— 有人开口, 会话就存在了.
每一轮顺带刷 `updated_at`, 会话列表按它倒序.

**取消 / 失败那一轮也要记** (见 `record_unfinished`): 用户确实说过那句话, 页面上
也显示了它, 记录里不该凭空少一轮 —— 那一轮记成「提问 + 一条「这一轮没答完」的
可见说明」, 运行行的状态是 cancelled / failed.

**写不进去怎么办** (2026-09-22 用户明确要求「至少让用户看到」): 这一句问答**绝不
因为记录写不进去而答不出来**. 于是写失败时:

1. 记一笔 warning 日志 (带 traceback, 排查用), 返回 False;
2. 在本记录员上留一个内存标记「这段会话有一轮没记上」;
3. **下一次这段会话成功写入时, 先补一条可见的提示行**再写本轮 —— 同一批插入
   (同一事务), 要么都成要么都不成. 提示行是 `role=system` + `hidden=False`, 正文
   见 `MISSED_TURN_TEXT`.

**诚实边界** (这是取舍, 不是完美方案): 写记录失败的同一时刻数据库大概率正不可用,
"当次就把提示写给用户看"很可能也写不进去; 内存标记活在进程里, 进程重启会丢;
而且**那一轮的内容补不回来** —— 提示行只说明「这里少了一轮」, 不假装能补上. 这是
在「只记日志」与「上分布式协调」之间的中间档: 代价小, 覆盖最常见的情形 (一次
抖动 / 一次约束冲突), 且不会把「记录缺了一轮」这件事悄悄咽掉.

**为什么这里不发警告事件**: 本模块跑在运行**收尾之后**, 而那条事件流已经被终局
事件关掉了 (`stream/bus.py` 的第四条不变量: final/error 之后不得再发任何事件;
`server/runs.py` 的 `RunStream` 还会把序号记账一起带偏). 想发就得破坏契约, 所以
给用户的信号走**记录表里那条提示行** (下一次写入时出现) —— 它比一条转瞬即逝的
事件更耐久: 前端刷新、换台设备都还看得见.

**为什么收尾的钩子挂在 `ChatSession` 上** (而不是 server 层或业务侧): 命令行入口
没有 HTTP 层, 放 server 就等于 CLI 没有记录; 放业务侧则要在框架的运行生命周期里
找钩子、靠事件流重建顺序 —— 脆. 会话是装配的唯一入口 (它已经拿着 `LoopResult`),
记录在这一处写, 两个入口同时覆盖.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Protocol

from CharAgent.agent.utils.types import LoopResult
from CharAgent.db.conversation import (
    TranscriptLine,
    has_visible_answer,
    recorded_transcript,
)
from CharAgent.db.entities import RunStatus
from CharAgent.db.errors import DbError
from CharAgent.db.repositories.base import Database
from CharAgent.db.repositories.messages import MessagesRepository
from CharAgent.db.repositories.runs import RunsRepository
from CharAgent.db.repositories.threads import ThreadsRepository
from CharAgent.db.state import run_status_for_outcome
from CharAgent.prompt import ref_name

# 同一棵日志树 (与 checkpoint 那几处同一个做法): 记不上账是**要有人知道**的事,
# 但它的严重程度不到「打断用户这一句」—— 所以是 warning 而不是异常
logger = logging.getLogger("charagent.db")

# 「上一轮没记上」那条提示行的正文 (落库时 `hidden=False`, 用户看得见)
MISSED_TURN_TEXT = "(这中间有一轮对话没能记录下来)"

# 「这一轮没答完」那条说明的正文: 取消 / 失败 / 跑完却没给出答复这三种收尾共用
# 同一句 —— 对看的人来说它们是同一件事 (这里少了一句答复), 而区别 (谁停的、
# 为什么停) 在运行行的 status 与事件流里
UNFINISHED_TURN_TEXT = "这一轮没答完"

# 会话标题的长度上限: 标题是给前端左侧列表一行的, 太长会被截成省略号, 不如我们
# 自己截 —— 完整那句话就是这段会话的第一条消息, 点进去看得到
TITLE_LIMIT = 60


@dataclass(frozen=True, slots=True)
class RunFacts:
    """运行行里除了「状态与时刻」之外的那些**账目**字段.

    为什么打成一个包而不是九个参数: 它们同源 (全从 `LoopResult` 派生) 而且必须
    **同进同出** —— 总量有值而分解为空, 那一行自己就说不通 (读的人会以为这次运行
    一个缓存命中的 token 都没有, 而真相是「没记」). 打成包之后, `_write` 的签名
    不必随着列的增加而变长, 而「一次运行的账」长什么样只有一个定义.

    空实例 (`RunFacts()`) 是**没跑完那一轮**的账: 取消 / 失败时确实什么都没有 ——
    这时五个分解字段都是 None (「没有」, 不是「零」).

    attributes:
        turn_count / total_tokens: 跑了几轮 / 累计用量 (账单原值).
        input_tokens / output_tokens / reasoning_tokens / cache_hit_tokens /
        cache_miss_tokens: 累计用量的**归因拆解** (#34). None = 上游一次都没上报
            过这个分量 (见 agent/utils/messages.py 的 accumulate_usage).
        prompt_version: 这一轮用的提示词名 (如 "system/v2"); None 表示装配时没给
            身份说明的引用 (框架不知道它是什么).
    """

    turn_count: int = 0
    total_tokens: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    prompt_version: str | None = None

    @classmethod
    def of(cls, result: LoopResult) -> RunFacts:
        """一次跑完的运行 → 它的账 (字段全部原样取自结果, 不在这里换算)."""
        return cls(
            turn_count=result.turn_count,
            total_tokens=result.total_tokens,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            reasoning_tokens=result.reasoning_tokens,
            cache_hit_tokens=result.cache_hit_tokens,
            cache_miss_tokens=result.cache_miss_tokens,
            # 提示词名取自已记录的引用: 内联 (老帧) 或没配时是 None —— 那种情况
            # 运行记录里如实留空, 不编一个名字出来
            prompt_version=ref_name(result.prompt_ref),
        )


class RunRecorder(Protocol):
    """SPI: 「把一轮运行记下来」这件事的形状 (ChatSession 认它, 不认具体实现).

    与框架里其他协议同一套做法 (ChatModel / CheckpointSaver / ContextProvider):
    只照形状检查, 不要求继承 —— 业务要换成写到别处 (对象存储 / 数仓) 自己实现这
    两个方法即可. 本模块的 `ConversationRecorder` 是给 `db/` 那几张表用的实现.

    两个方法都**不该抛** (返回 False 表示这次没记上): 调用方是会话收尾那条路,
    记录写不进去不该把一次已经跑完的问答变成一次失败. 实现方自己负责记日志与补救.
    """

    async def record(
        self,
        *,
        thread_id: str,
        result: LoopResult,
        since: int = 0,
        summary: str | None = None,
    ) -> bool:
        """记下**一次跑完的运行** (含 visible / hidden 消息与运行行).

        Args:
            thread_id: 算哪段会话.
            result: 这次运行的结果 (`LoopResult`) —— 记录的内容全部由它派生.
            since: 只从消息列表的第几条开始记 (跑之前历史有多长) —— 老的那一段
                上一次已经写过了, 重记一遍会写出重复行.
            summary: 这一轮**新压出来的**摘要; None 表示这一轮没压 (或压出来的与
                上一轮那份一样). 调用方判「新不新」而实现不自己比 —— 比较基准
                (上一轮那份) 只有会话手上有.

        Returns:
            bool: 记上了 True; 没记上 (库不可用等) False.
        """
        ...

    async def record_unfinished(
        self, *, thread_id: str, question: str, status: RunStatus
    ) -> bool:
        """记下**一次没答完的运行** (取消 / 失败那一轮).

        Args:
            thread_id: 算哪段会话.
            question: 用户那一句提问 (页面上已经显示了, 记录里不能少).
            status: 这次运行的终态 (cancelled / failed).

        Returns:
            bool: 记上了 True; 没记上 False.
        """
        ...


class ConversationRecorder:
    """把会话记录写进 `db/` 那几张表 (框架自带的 RunRecorder 实现).

    身份是**装配时绑好的**: 一个实例只服务一段会话的属主, 于是写入时不必每次
    传租户与用户 (也不可能传错). 会话编号仍然按次传 —— 一个进程里几十段会话在
    同时聊, 而记录员只该有一个.

    Args:
        database: 库入口 (`Database` 协议: 能开一次事务就行).
        tenant_id: 这些记录属于哪个租户 (会话行懒创建时写进去).
        user_id: 会话属主 (同上).

    attributes:
        (无公开属性; `missed_threads` 是给排查与用例看的只读视图)
    """

    def __init__(self, *, database: Database, tenant_id: str, user_id: str) -> None:
        self._threads = ThreadsRepository(database)
        self._runs = RunsRepository(database)
        self._messages = MessagesRepository(database)
        self._tenant_id = tenant_id
        self._user_id = user_id
        # 「这段会话有一轮没记上」的内存标记: 下一次成功写入时先补一条提示行.
        # 活在进程里 —— 进程重启会丢, 这条边界写在模块 docstring 里了
        self._missed: set[str] = set()

    @property
    def missed_threads(self) -> frozenset[str]:
        """有哪些会话欠着一条「没记上」的提示行 (排查与用例用)."""
        return frozenset(self._missed)

    async def record(
        self,
        *,
        thread_id: str,
        result: LoopResult,
        since: int = 0,
        summary: str | None = None,
    ) -> bool:
        """记下这次跑完的运行 (消息按可见性落库 + 运行行 + 刷会话的活动时刻)."""
        lines = recorded_transcript(result.messages, result.content, since=since)
        if summary:
            # 这一轮把早前的对话压成了摘要: 它是**内部件** (给模型看的上下文替身),
            # 于是 hidden=True —— 但留下它, 审计时能看出「模型当时看到的是什么」.
            # 写在这里 (而不是每次运行都写一遍): 摘要不变就不记, 否则一段长会话
            # 会攒下几十条一模一样的行.
            lines.append(
                TranscriptLine(
                    role="system",
                    content=summary,
                    reasoning=None,
                    hidden=True,
                )
            )
        if not has_visible_answer(lines):
            # 跑完了却一条可见答复都没有 (guard 刹车 / 纯工具收尾): 补一句说明.
            # 与「没答完」那条路同一条规矩 —— 页面上那一轮不该看起来凭空消失
            lines.append(
                TranscriptLine(
                    role="system",
                    content=UNFINISHED_TURN_TEXT,
                    reasoning=None,
                    hidden=False,
                )
            )
        return await self._write(
            thread_id=thread_id,
            lines=lines,
            status=run_status_for_outcome(result.outcome),
            facts=RunFacts.of(result),
        )

    async def record_unfinished(
        self, *, thread_id: str, question: str, status: RunStatus
    ) -> bool:
        """记下这一轮没答完 (提问 + 一条可见说明) —— 取消 / 失败那一轮走这里."""
        lines = [
            TranscriptLine(role="user", content=question, reasoning=None),
            TranscriptLine(
                role="system",
                content=UNFINISHED_TURN_TEXT,
                reasoning=None,
                hidden=False,
            ),
        ]
        # 没跑完那一轮没有账可记: 空实例 = 五个分解字段都是 None (「没有」, 不是「零」)
        return await self._write(
            thread_id=thread_id, lines=lines, status=status, facts=RunFacts()
        )

    async def _write(
        self,
        *,
        thread_id: str,
        lines: list[TranscriptLine],
        status: RunStatus,
        facts: RunFacts,
    ) -> bool:
        """四步写入 (会话行 / 运行行 / 消息行 / 活动时刻), 失败降级不抛.

        为什么是四次分开的事务而不是一个大事务: 四件事属于三个仓储 (各管一张表),
        凑成一个大事务要么跨仓储开私用接口, 要么把这四张表的写法搬到本模块 ——
        两条都比「四步顺序写」更难维护. 代价是**中间失败会留下半截记录** (比如运行
        行写了、消息行没写), 那一轮同样落进 `_missed`, 下一次补提示行 —— 记录表
        里不会出现「看起来完整其实缺了半轮」的样子 (提示行会说话).

        只兜 `DbError` (数据库那一族的错, 仓储抛的就是它): 别的异常是框架自己的
        bug, 该留 traceback 给人看 —— 悄悄吞掉会让记录从此错下去, 那比一次报错
        难查得多.
        """
        try:
            await self._ensure_thread(thread_id, lines)
            run = await self._runs.add_terminal(
                thread_id=thread_id, status=status, **asdict(facts)
            )
            # 消息行带上 run_id: 它们确实属于刚建的那次执行 —— 审计时「这几条是
            # 哪一次问答产生的」靠它 (列注释: NULL 表示不是 agent 跑出来的)
            #
            # 提示行与本轮那几行**同一批**插进去: 要么都成, 要么都不成 ——
            # 否则会出现「说了缺一轮, 但本轮也没写进去」这种更乱的中间态
            await self._messages.add_lines(
                thread_id=thread_id,
                lines=self._pending(thread_id, lines),
                run_id=run.run_id,
            )
            await self._threads.touch(thread_id)
        except DbError as exc:
            self._missed.add(thread_id)
            logger.warning(
                "会话 %s 这一轮没能记进记录表: %s", thread_id, exc, exc_info=True
            )
            return False
        self._missed.discard(thread_id)
        return True

    async def _ensure_thread(self, thread_id: str, lines: list[TranscriptLine]) -> None:
        """会话行懒创建: 有就复用, 没有就用首条用户消息当标题建一个.

        `get` 再 `add` 之间有一个窗口, 但框架的部署纪律本就是**单进程**
        (`server/sessions.py` 写明), 而同一段会话又不许并发 (登记表的 busy 集合
        拦着) —— 于是这两步之间不会有第二个写入者.
        """
        existing = await self._threads.get(thread_id)
        if existing is not None:
            if (existing.tenant_id, existing.user_id) != (
                self._tenant_id,
                self._user_id,
            ):
                # 同一段会话编号换了属主 = 业务把两段对话编到了同一个键上 (编号是
                # 快照与记录**共用的分区键**, 换了属主不会自动分开). 框架不替业务
                # 决定该不该写 (不写就是静默丢记录), 但要让这件事看得见
                logger.warning(
                    "会话 %s 已经属于 (%s, %s), 而这次记账的身份是 (%s, %s): "
                    "记录会写进同一段会话, 检查会话编号的拼法",
                    thread_id,
                    existing.tenant_id,
                    existing.user_id,
                    self._tenant_id,
                    self._user_id,
                )
            return
        await self._threads.add(
            thread_id=thread_id,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            title=title_for(lines),
        )

    def _pending(
        self, thread_id: str, lines: list[TranscriptLine]
    ) -> list[TranscriptLine]:
        """要落库的那些行: 欠着提示行时**先补**它, 再写本轮 (同一批)."""
        if thread_id not in self._missed:
            return lines
        return [
            TranscriptLine(role="system", content=MISSED_TURN_TEXT, reasoning=None),
            *lines,
        ]


def title_for(lines: list[TranscriptLine]) -> str:
    """会话标题: 这批行里第一条用户消息 (压平空白 + 截断).

    为什么要压平空白: 标题在前端是**一行**, 而用户的提问可能带换行 (粘贴一段
    报错、写几条要点) —— 原样存进去会把列表那一行撑成好几行.

    Args:
        lines: 要落库的那批行 (顺序即时间顺序).

    Returns:
        str: 标题; 这批行里没有用户消息时是空串 (``add`` 的约定: 空串表示还没起名).
    """
    for line in lines:
        if line.role == "user" and line.content:
            return " ".join(line.content.split())[:TITLE_LIMIT]
    return ""


__all__ = [
    "MISSED_TURN_TEXT",
    "TITLE_LIMIT",
    "UNFINISHED_TURN_TEXT",
    "ConversationRecorder",
    "RunFacts",
    "RunRecorder",
    "title_for",
]
