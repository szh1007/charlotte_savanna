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

**什么时候写** (ticket 27 起是「产生即落库」):

| 时机 | 谁写 | 写什么 |
|---|---|---|
| 运行开始 | `begin` | 运行行 (状态 running) —— 帧的 `run_id` 外键指着它 |
| 运行进行中 | `flush` (可选, 见下) | 提问行 · 每轮新产生的消息 · 每条调用 |
| | | (执行前 pending、执行后终态) |
| 运行收尾 | `record` / `record_unfinished` | **补齐**这一轮该有的行 (幂等) |
| | | · **修订** (截断续写的几段合成一条) |
| | | · 运行行终态 · 会话活动时刻 |

> **收尾不收运行行的那一种情形** (ticket 33 补): HITL 的挂起被批准之后, 同一次运行
> 会**接着跑第二段**. 第二段跑完时那一次运行还没结束 (可能再挂起, 也可能就此收尾)
> —— 该往那一行写什么只有收尾的那一段知道. 于是两个收尾入口各多一个 `finish_run`
> 开关, 关掉它时上表最后一格里的「运行行终态」不做, 消息 / 调用 / 活动时刻照做.

**为什么从「收尾一次性写」改成「产生即落库」** (2026-09-24, ticket 27):
`charagent_tool_calls.message_id` 是指向消息行的**外键**, 而那条 assistant 行今天只在
收尾才写、编号还是插入时随机生成的 —— 于是「工具调用行在执行前落 pending、挂起那条
在挂起那一刻就落库」(ADR-0014 要的判据) 在类型上就做不到. 改成产生即落库之后, 消息
行在**产生时**就在库里, 编号由 `(run_id, 下标)` 派生 (`messages.message_id_for`),
于是运行中写得进去、同一行写两次是幂等的、而同一次运行的第二段 (审批恢复) 能直接
寻址第一段写下的那一行, 不必反查.

**`flush` 是可选的**: 它由 loop 在两处调用 (工具执行前 / 每轮收尾), 而 loop 只认
`agent.utils.types.TraceSink` 那份协议 —— 业务自己的记录员不实现它就退回「收尾一次
性写」, 行为与从前逐字一样 (见 `RunRecorder` 的说明).

**这一改不碰「记录只增不减」**: 收尾的**修订**只改正文与可见性 (截断续写的几段合成
一条, 早先那几段退成隐藏行), 行本身一条不多一条不少; **补齐**只是把没写上的补上
(幂等), 已有的行一个字不改.

**写什么** (三层都由 `db/conversation.py` 的规则算好, 本模块不另判一遍):

| 落库的 | 内容 |
|--------|------|
| `runs` 一行 | 这次运行的结局 (`RUN_STATUS_FOR_OUTCOME` 映射的终态) 与轮次 / |
| | 用量 —— 用量含**分解** (输入 / 输出 / 思维链 / 缓存命中 / 未命中), 以及 |
| | 模型名与提示词版本 (版本归因 #40), 见 `RunFacts` |
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
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from CharAgent.agent.utils.types import (
    LoopResult,
    ToolCallFact,
    ToolCallOutcome,
)
from CharAgent.db.conversation import (
    TranscriptLine,
    has_visible_answer,
    recorded_transcript,
    visible_transcript,
)
from CharAgent.db.cost import (
    CostGap,
    PriceTable,
    RunCost,
    cost_of,
    ensure_pricing_ready,
    load_pricing,
)
from CharAgent.db.entities import RunStatus, ToolCallStatus
from CharAgent.db.errors import DbError
from CharAgent.db.repositories.base import Database
from CharAgent.db.repositories.messages import (
    MessagesRepository,
    message_id_for,
)
from CharAgent.db.repositories.runs import RunsRepository
from CharAgent.db.repositories.threads import ThreadsRepository
from CharAgent.db.repositories.tool_calls import (
    ToolCallsRepository,
    build_tool_call,
)
from CharAgent.db.state import run_status_for_outcome, tool_call_status_for_outcome
from CharAgent.model.utils.types import ModelMessage
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

    空实例 (`RunFacts()`) 是**没跑完那一轮**的账: 取消 / 失败时确实没有账目可记
    —— 五个分解字段都是 None (「没有」, 不是「零」), 而模型名是**跑之前就定下的
    配置事实**, 那一轮照样带得上 (见 `record_unfinished`).

    attributes:
        turn_count / total_tokens: 跑了几轮 / 累计用量 (账单原值).
        input_tokens / output_tokens / reasoning_tokens / cache_hit_tokens /
        cache_miss_tokens: 累计用量的**归因拆解** (#34). None = 上游一次都没上报
            过这个分量 (见 agent/utils/messages.py 的 accumulate_usage).
        prompt_version: 这一轮用的提示词名 (如 "system/v2"); None 表示装配时没给
            身份说明的引用 (框架不知道它是什么).
        model: 这一轮用的模型名 (与发给 API 的逐字一致, 见
            `prompt/load.py` 的 `resolve_model_name`); None 表示调用方没给.
    """

    turn_count: int = 0
    total_tokens: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    prompt_version: str | None = None
    model: str | None = None

    @classmethod
    def of(cls, result: LoopResult, *, model: str | None = None) -> RunFacts:
        """一次跑完的运行 → 它的账 (账目字段全部原样取自结果, 不在这里换算).

        模型名是唯一例外: 结果里没有它, 由调用方另给 (见 Args).

        Args:
            result: 这次运行的结果.
            model: 这次用的模型名 —— 结果里没有它 (loop 手上是个薄协议的模型对象,
                名字只有装配处知道), 所以由调用方递进来.
        """
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
            model=model,
        )


@dataclass(frozen=True, slots=True)
class RunSettlement:
    """运行行**收尾那一笔**: 这一段跑完之后, 那一行长什么样.

    为什么打成一个包 (与 `RunFacts` 同一条理由): 这四样**必须同进同出** —— 状态说
    「这次跑成什么样」、账目说「花在哪儿」、金额说「折成多少钱」、最后一帧说「当时
    它看到什么」, 少一样那一行自己就说不通. 打成包之后, 「这一段不收尾」于是也
    只要说一次: 传 None (见 `_write`).

    **None 的那一种情形** (HITL 的第二段, ticket 33): 这一段跑完了, 但**这一次运行**
    还没结束 (可能再挂起, 也可能就此收尾) —— 那一行该写什么只有收尾那一段知道,
    于是这一段一笔都不碰它. 账目与金额不会因此丢: 续跑接着数计数器, 收尾那一段
    拿到的结果里就是这一整趟的累计值.

    attributes:
        status: 这一段跑完时那一行的状态 (由 outcome 推出来, 见 `_write` 的调用方).
            **不一定是终态**: 挂起等人那一段落的是 `waiting_user` —— 这一段的结局
            就是「停在这儿等人」, 而那一行还开着 (人给了结论才接着跑).
        facts: 这一趟的账目 (续跑段给的是**累计**值 —— 计数器从快照接着数).
        cost: 这一趟的钱 (算好的算式, 或算不出来的原因).
        last_checkpoint_id: 本段落下的最后一帧; None = 这一段没落帧 (或调用方没给)
            —— 它是「顺 parent_id 往回走 = 本次运行落的每一帧」的起点.
    """

    status: RunStatus
    facts: RunFacts
    cost: RunCost
    last_checkpoint_id: str | None = None


class RunRecorder(Protocol):
    """SPI: 「把一轮运行记下来」这件事的形状 (ChatSession 认它, 不认具体实现).

    与框架里其他协议同一套做法 (ChatModel / CheckpointSaver / ContextProvider):
    只照形状检查, 不要求继承 —— 业务要换成写到别处 (对象存储 / 数仓) 自己实现这
    几个方法即可. 本模块的 `ConversationRecorder` 是给 `db/` 那几张表用的实现.

    **三个方法, 对应一次运行的三拍** (ticket 22 起):

    | 方法 | 什么时候 | 做什么 |
    |---|---|---|
    | `begin` | 跑之前 | 把这一轮的运行行先建出来 (状态 running), 返回编号 |
    | `record` | 跑完了 | 推进那一行到终态 + 写消息行 + 落 `last_checkpoint_id` |
    | `record_unfinished` | 没跑完 (取消 / 失败) | 同上, 只是终态不同、没有账目 |

    后两个各有一个 `finish_run` 开关 (ticket 33 补): 关掉它 = 这一段跑完了, 但
    **这一次运行**还没结束 (HITL 挂起之后续跑的那一段). 那时消息 / 调用 / 会话活动
    时刻照写, 运行行一笔不碰 —— 那一行横跨挂起等待期, 该写什么由收尾的那一段说
    (见 `RunSettlement` 的 None 那一段).

    为什么「先建行」: 快照帧是在运行**中途**逐轮落盘的, 而 `checkpoints.run_id` 是
    指向运行行的外键 —— 行不在, 帧就盖不上编号 (见 `begin` 的说明).

    三个方法都**不该抛** (`begin` 返回 None、另两个返回 False 表示这次没记上): 调用
    方是会话那条路, 记录写不进去不该把一次问答变成一次失败. 实现方自己负责记日志
    与补救.

    **还可以多实现一个 `TraceSink`** (ticket 27): 那是 agent 侧的协议
    (`agent.utils.types.TraceSink` 的 `flush`), 实现了就会在运行**进行中**收到
    「刚产生的这几条消息与这批工具调用」—— 于是记录表在运行中途就跟着事实走.
    不实现也完全成立: 那三个方法照旧把整轮一次写完, 只是轨迹要等到收尾才看得到.
    `ConversationRecorder` 两个都实现了, 而会话装配时按 `isinstance(recorder,
    TraceSink)` 决定要不要把它交给 loop (结构匹配, 不要求继承).
    """

    async def begin(self, *, thread_id: str, title: str = "") -> str | None:
        """开一次运行的账: 建行 (状态 running), 返回编号.

        Args:
            thread_id: 哪段会话 (会话行不在就顺手建).
            title: 会话标题的候选 (通常是这次提问); **只在会话行还不存在时用得上**
                —— 标题是首条用户消息 (与从前一致), 后面的轮次给了也不覆盖.

        Returns:
            str | None: 运行编号; None 表示这次不记账 (库不可用等) —— 于是这一轮的
            帧不带 `run_id`、也没有运行行, 与「没配记录层」同一种形状.
        """
        ...

    async def record(
        self,
        *,
        thread_id: str,
        result: LoopResult,
        run_id: str | None = None,
        since: int = 0,
        summary: str | None = None,
        model: str | None = None,
        finish_run: bool = True,
    ) -> bool:
        """记下**一次跑完的运行** (含 visible / hidden 消息 + 推进运行行).

        Args:
            thread_id: 算哪段会话.
            result: 这次运行的结果 (`LoopResult`) —— 记录的内容全部由它派生.
            run_id: `begin` 那一步返回的编号 (要推进的那一行); None 表示这一轮
                没有可推进的行 (没配记录层 / `begin` 没成) → 直接返回 False.
            since: 只从消息列表的第几条开始记 (跑之前历史有多长) —— 老的那一段
                上一次已经写过了, 重记一遍会写出重复行.
            summary: 这一轮**新压出来的**摘要; None 表示这一轮没压 (或压出来的与
                上一轮那份一样). 调用方判「新不新」而实现不自己比 —— 比较基准
                (上一轮那份) 只有会话手上有.
            model: 这次用的模型名 —— 结果里没有它 (loop 手上是个薄协议的模型对象,
                名字只有装配处知道), 所以由调用方递进来; None 表示没给.
            finish_run: 要不要把运行行结掉. False = 这一段跑完了但那次运行还没结束
                (HITL 续跑的第二段): 消息 / 调用 / 会话活动照写, 运行行一笔不碰.

        Returns:
            bool: 记上了 True; 没记上 (库不可用 / 没有那一行) False.
        """
        ...

    async def record_unfinished(
        self,
        *,
        thread_id: str,
        question: str | None,
        status: RunStatus,
        run_id: str | None = None,
        since: int = 0,
        model: str | None = None,
        finish_run: bool = True,
    ) -> bool:
        """记下**一次没答完的运行** (取消 / 失败那一轮).

        Args:
            thread_id: 算哪段会话.
            question: 用户那一句提问 (页面上已经显示了, 记录里不能少); **None =
                这一段不是提问触发的** (续跑段): 那一段没有「用户说过的话」可写,
                于是记录里只留一句「这一轮没答完」.
            status: 这次运行的终态 (cancelled / failed).
            run_id: 同 `record` —— `begin` 建的那一行; None 表示没得推进.
            since: 那一句提问在本次 run wire 历史里的下标. 提问行**在提问时就已经
                落过库** (ticket 27 起由 `flush` 写), 这里给下标是为了让它落在同一
                行上而不是再写一条 (编号是算出来的) —— 给错会多出一条重复的提问行.
                `question` 为 None 时它用不上 (没有从 wire 历史来的行).
            model: 这次用的模型名 (同 `record`); 这一轮没有账目, 但模型是跑之前
                就定下的配置事实, 照样记得下来.
            finish_run: 同 `record` —— False = 这一段没收尾 (那一次运行还没结束).

        Returns:
            bool: 记上了 True; 没记上 False.
        """
        ...


class ConversationRecorder:
    """把会话记录写进 `db/` 那几张表 (框架自带的 RunRecorder 实现).

    身份是**装配时绑好的**: 一个实例只服务一段会话的属主, 于是写入时不必每次
    传租户与用户 (也不可能传错). 会话编号仍然按次传 —— 一个进程里几十段会话在
    同时聊, 而记录员只该有一个.

    **花钱这笔账也算在这里** (ticket 28 起): 运行收尾那一拍, 本类按**当时的价目表**
    把金额算好、连同算式一起写进运行行 (`db/cost.py` 是那套算法). 价目表在构造时
    读一次 —— 一次会话里的几十趟运行因此看的是同一版价 (运行到一半价目表被改了,
    不该让同一段会话前后两趟用两个价).

    Args:
        database: 库入口 (`Database` 协议: 能开一次事务就行).
        tenant_id: 这些记录属于哪个租户 (会话行懒创建时写进去).
        user_id: 会话属主 (同上).
        prices: 价目表; None = 从 `CHARAGENT_MODEL_PRICES` 读一次. 传进来是给
            测试与「价目表来自别处」的装配用的.

    attributes:
        (无公开属性; `missed_threads` 是给排查与用例看的只读视图)
    """

    def __init__(
        self,
        *,
        database: Database,
        tenant_id: str,
        user_id: str,
        prices: PriceTable | None = None,
    ) -> None:
        self._threads = ThreadsRepository(database)
        self._runs = RunsRepository(database)
        self._messages = MessagesRepository(database)
        self._calls = ToolCallsRepository(database)
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._prices = self._load_prices(prices)
        # 「这段会话有一轮没记上」的内存标记: 下一次成功写入时先补一条提示行.
        # 活在进程里 —— 进程重启会丢, 这条边界写在模块 docstring 里了
        self._missed: set[str] = set()

    @staticmethod
    def _load_prices(prices: PriceTable | None) -> PriceTable:
        """拿到这一版价目表: 读配置 + 自检; 读不成 / 没准备好就当场抛.

        为什么**抛**而不是像写入那样降级: 这两个坑 (配置写错、日历过期) 都是
        「再跑一百趟也一样算不出钱」, 而且都在**开跑之前**就知道 —— 与其让每一趟
        的钱都空着、事后人工补, 不如在构造这一刻拦住 (业务侧那条启动自检调的是
        同一个函数, 见 `db/cost.py` 的 `ensure_pricing_ready`).

        Args:
            prices: 调用方给的价目表; None = 自己从环境变量读.

        Returns:
            PriceTable: 这一版价目表 (可能是一张空表 —— 没配价不是错误).

        Raises:
            PricingNotReadyError: 配置写错, 或配了峰谷价却判不了今天.
        """
        if prices is None:
            return load_pricing()
        ensure_pricing_ready(prices)
        return prices

    @property
    def missed_threads(self) -> frozenset[str]:
        """有哪些会话欠着一条「没记上」的提示行 (排查与用例用)."""
        return frozenset(self._missed)

    async def begin(self, *, thread_id: str, title: str = "") -> str | None:
        """开一次运行的账 (建会话行 + 建运行行), 返回运行编号.

        为什么在**跑之前**就把行建出来 (ticket 22): 快照帧是在运行中途逐轮落盘的,
        而 `charagent_checkpoints.run_id` 是指向这一行的外键 —— 行不在, 帧就盖不上
        编号, 那条「这一帧属于哪一行账」的溯源就是空的. 于是编号提前定下来, 由会话
        同时交给 loop (盖到每一帧上) 与收尾的 `record` (推进这一行).

        状态直接给 **running** 而不是 created: 建它的那一刻这次运行真的开始了
        (调用方紧接着就调 loop), 而状态机里 created 到不了 finished (见 state.py
        的合法迁移表) —— 写成 created 就得为「跑完」多补一次没意义的中间推进.

        失败只降级不抛 (与另两个方法同一条规矩): 记不上账不该让用户这一句问不出来.
        这一轮于是没有运行行、帧上的 `run_id` 为空, 与「没配记录层」同一种形状;
        事后由 `_missed` + 下一次成功写入的提示行如实报一句.

        Args:
            thread_id: 哪段会话 (会话行不在就顺手建, 标题用 title).
            title: 会话标题的候选 (通常是这次提问); 只在会话行还不存在时生效.

        Returns:
            str | None: 运行编号; None = 这次不记账.
        """
        # 开始时刻**在这里定下来**, 并且一路用到两处: 写进运行行 (`created_at`)
        # 与收尾算钱时判峰谷 —— 两处必须是同一个值, 否则「这笔按峰还是谷算」与
        # 「这一趟什么时候开始的」会对不上
        moment = datetime.now(UTC)
        try:
            await self._ensure_thread(thread_id, normalize_title(title))
            run = await self._runs.add(
                thread_id=thread_id, status=RunStatus.RUNNING, created_at=moment
            )
        except DbError as exc:
            self._missed.add(thread_id)
            logger.warning(
                "会话 %s 这一轮的账没能开出来 (本轮不记账): %s",
                thread_id,
                exc,
                exc_info=True,
            )
            return None
        return run.run_id

    async def record(
        self,
        *,
        thread_id: str,
        result: LoopResult,
        run_id: str | None = None,
        since: int = 0,
        summary: str | None = None,
        model: str | None = None,
        finish_run: bool = True,
    ) -> bool:
        """记下这次跑完的运行 (消息按可见性落库 + 推进运行行 + 刷会话的活动时刻)."""
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
        # 不结账 (HITL 续跑的第二段) 时这一笔整个不给: 状态 / 账目 / 金额 / 最后一帧
        # 都不写, 也不去算钱 —— 算了也没地方放 (见 RunSettlement)
        settlement: RunSettlement | None = None
        if finish_run:
            facts = RunFacts.of(result, model=model)
            settlement = RunSettlement(
                status=run_status_for_outcome(result.outcome),
                facts=facts,
                # 只有真的开了账 (`begin` 建出那一行) 才算钱: 没有那一行就没有开始
                # 时刻, 也就判不了峰谷 —— `_write` 会按「没账目」整轮不写
                cost=await self._cost_of(facts, run_id)
                if run_id is not None
                else RunCost(gap=CostGap.NO_MOMENT, model=model),
                # 本段落的最后一帧: 有了它, 「这次花了多少」与「当时它看到了什么」就
                # 对到同一件事上 (顺 parent_id 往回走 = 本次运行落的每一帧)
                last_checkpoint_id=result.last_checkpoint_id,
            )
        return await self._write(
            thread_id=thread_id,
            lines=lines,
            run_id=run_id,
            settlement=settlement,
            # 工具调用的事实全在逐轮记录里 (运行中那两拍用的也是它): 这里一并交给
            # 写入路径 —— 缺的行补上, 有结论的推进到终态 (见 _write_calls)
            calls=[fact for turn in result.turns for fact in turn.calls],
            since=since,
            # 「产生时那一拍写下的形态」: 与上面那份目标形态比一比, 不同的才需要
            # 修订 (截断续写时几段合成一条 / 早先那几段退成隐藏行)
            produced=visible_transcript(result.messages, since=since),
        )

    async def record_unfinished(
        self,
        *,
        thread_id: str,
        question: str | None,
        status: RunStatus,
        run_id: str | None = None,
        since: int = 0,
        model: str | None = None,
        finish_run: bool = True,
    ) -> bool:
        """记下这一轮没答完 (提问 + 一条可见说明) —— 取消 / 失败那一轮走这里."""
        # 提问那一行只有「由提问触发的运行」才有 (question=None 是续跑段: 那一段
        # 起于一次人工批准, 没有谁说过的哪句话可以写)
        asked = (
            [TranscriptLine(role="user", content=question, reasoning=None)]
            if question is not None
            else []
        )
        lines = [
            *asked,
            TranscriptLine(
                role="system",
                content=UNFINISHED_TURN_TEXT,
                reasoning=None,
                hidden=False,
            ),
        ]
        # 没跑完那一轮没有账可记: 五个分解字段都是 None (「没有」, 不是「零」) ——
        # 但模型名照样带上: 它是跑之前就定下的配置事实, 不是跑出来的账目.
        # 不结账 (续跑段) 时连这一笔都不给 —— 那一行横跨挂起等待期, 该写什么由
        # 收尾的那一段说
        settlement = (
            RunSettlement(
                status=status,
                facts=RunFacts(model=model),
                # 没跑完那一轮没有账目 (五列全是 None), 也就没有金额 —— 明细里
                # 如实写一句原因, 而不是让那一列空着不解释
                cost=RunCost(gap=CostGap.UNFINISHED, model=model),
            )
            if finish_run
            else None
        )
        return await self._write(
            thread_id=thread_id,
            lines=lines,
            run_id=run_id,
            settlement=settlement,
            # 提问那一行**在提问时就已经落过库了** (ticket 27 起): 这里要它落在
            # 同一行上 —— 给下标就够, 编号是算出来的 (写第二遍是幂等的).
            # 没有提问 (续跑段) 就一条都不来自 wire 历史, 编号只能随机给
            since=since,
            produced=[asked[0]] if asked else None,
        )

    async def flush(
        self,
        *,
        thread_id: str,
        run_id: str,
        start: int,
        messages: Sequence[ModelMessage],
        calls: Sequence[ToolCallFact] = (),
    ) -> None:
        """运行**进行中**把刚产生的那几条落库 (`TraceSink` 的实现, ticket 27).

        两处会调到它 (都在 loop 里): **工具执行前** (那条 assistant 隐藏行 + 这几条
        调用的 `pending`) 与**每轮收尾** (这一轮新产生的消息 + 有结论的那几条调用).
        于是库里在运行中途就跟着事实走 —— 执行中被硬杀 / 被取消时看得出它正要调
        什么, 挂起那条在挂起那一刻就在库里 (ADR-0014 的判据).

        **写失败只降级、不抛、也不留「欠一条」的标记**: 收尾那一拍 (`record`) 会把
        这一轮该有的行**补齐** (幂等), 所以此刻没写上不是「记录缺了一行」, 下一次
        写入时补一条用户可见的提示反而是误报 (行最后并不缺). 只有收尾也失败时,
        `_missed` 那条老机制才说话.

        Args:
            thread_id: 哪段会话 (会话行由 `begin` 建好了 —— loop 只在开了账时调它).
            run_id: 哪次运行.
            start: `messages` 的第 0 条在本次 run wire 历史里的下标 (算编号用).
            messages: 这一次新增的 wire 消息.
            calls: 与它们相关的调用事实.
        """
        if not messages and not calls:
            return
        try:
            await self._write_lines(
                thread_id=thread_id,
                run_id=run_id,
                messages=messages,
                start=start,
            )
            await self._write_calls(run_id=run_id, calls=calls)
        except DbError as exc:
            logger.warning(
                "会话 %s 这一段运行中途没能记进记录表 (收尾会补齐): %s",
                thread_id,
                exc,
                exc_info=True,
            )

    async def _write_lines(
        self,
        *,
        thread_id: str,
        run_id: str,
        messages: Sequence[ModelMessage],
        start: int,
    ) -> None:
        """把这一批 wire 消息落库 (按**产生时**的形态, 编号由下标派生).

        为什么按产生时的形态 (而不是收尾那份目标形态): 收尾才知道这一轮答复的最终
        形态 (截断续写的几段要合成一条), 而运行中只能如实写当下那一条 —— 修订留给
        收尾 (`_write` 的那一步), 于是「库里什么时候有什么」是按事实走的.
        """
        if not messages:
            return
        lines = visible_transcript(messages)
        await self._messages.add_lines(
            thread_id=thread_id,
            lines=lines,
            run_id=run_id,
            ids=[
                message_id_for(run_id, start + offset) for offset in range(len(lines))
            ],
        )

    async def _cost_of(self, facts: RunFacts, run_id: str) -> RunCost:
        """这一次运行花了多少钱 (按这一版价目表 + 它的开始时刻算).

        开始时刻**从运行行里读** (`created_at`) —— 不放在内存里: 判峰谷看的是那
        一刻, 而「哪一行、什么时候开始的」本来就记在库里. 记在内存里会多出一个
        说不清的依赖: 同一行被**另一个记录员实例**收尾时 (挂起补做 / 对账补齐 /
        换了个进程), 内存里那份是空的, 峰谷部署下金额就会静默留空.

        Args:
            facts: 这一次运行的账目 (五列用量 + 模型名).
            run_id: 哪一行 (开始时刻与它绑在一起).

        Returns:
            RunCost: 金额与算式, 或者算不出来的原因 (读不到那一行 -> `NO_MOMENT`).
        """
        run = await self._runs.get(run_id)
        return cost_of(
            model=facts.model,
            table=self._prices,
            moment=run.created_at if run is not None else None,
            input_tokens=facts.input_tokens,
            cache_miss_tokens=facts.cache_miss_tokens,
            cache_hit_tokens=facts.cache_hit_tokens,
            output_tokens=facts.output_tokens,
        )

    async def _write(
        self,
        *,
        thread_id: str,
        lines: list[TranscriptLine],
        run_id: str | None,
        settlement: RunSettlement | None,
        calls: Sequence[ToolCallFact] = (),
        since: int = 0,
        produced: Sequence[TranscriptLine] | None = None,
    ) -> bool:
        """六步写入 (会话行 / 运行行收尾 / 消息补齐 / 修订 / 调用行 / 活动时刻).

        失败降级不抛 (与另两个入口同一条规矩).

        这一步是**收尾**, 它的角色从 ticket 27 起变了: 不再是「把整轮一次写出来」,
        而是「把这一轮该有的行**补齐** + 把产生时那一拍写下的形态**修订**成最终形态」.
        运行中途那两拍 (`flush`) 已经把大部分行写过了, 于是这里的两次写都是幂等的:
        消息按派生编号写 (已有的跳过, `add_lines`), 调用先补行再推进终态.

        为什么是分开的几次事务而不是一个大事务: 几件事分属四个仓储 (各管一张表),
        凑成一个大事务要么跨仓储开私用接口, 要么把这四张表的写法搬到本模块 ——
        两条都比「顺序写」更难维护. 代价是**中间失败会留下半截记录** (比如运行行
        写了、消息行没写), 那一轮同样落进 `_missed`, 下一次补提示行 —— 记录表里
        不会出现「看起来完整其实缺了半轮」的样子 (提示行会说话).

        运行行那一笔是**推进** `begin` 建的那一行 (ticket 22), 不是新建: 帧在运行
        中途就已经把编号盖上了, 这里另起一行的会让那些帧指到别处去. `run_id` 为
        None 说明 `begin` 那一步没成 —— 那就整轮不写 (连消息行也不写: 消息行的
        `run_id` 列是外键), 落进 `_missed` 如实报一句.

        **`settlement` 为 None 时那一行不动** (HITL 续跑的第二段, ticket 33): 消息 /
        调用 / 会话活动时刻照写 (它们记的是**事实**, 与运行行结没结账无关), 只有
        运行行那一笔跳过 —— 它横跨挂起等待期, 该写什么由收尾的那一段说.

        只兜 `DbError` (数据库那一族的错, 仓储抛的就是它): 别的异常是框架自己的
        bug, 该留 traceback 给人看 —— 悄悄吞掉会让记录从此错下去, 那比一次报错
        难查得多.

        Args:
            thread_id / lines / run_id: 见本模块 docstring.
            settlement: 运行行收尾那一笔 (状态 / 账目 / 金额 / 最后一帧); None =
                这一段不收尾 (那一次运行还没结束). 为什么由**调用方**给而不是这里
                从 `lines` 推: 「跑完了没跑完」只有调用方知道 (空账目既可能是「没
                跑完」, 也可能是「跑完了但一点用量都没有」), 让这里去猜会用错那边.
            calls: 这一轮的工具调用事实 (含运行中没写上、要在这儿补的那些).
            since: `lines` 的第 0 条在本次 run wire 历史里的下标 (算编号用).
            produced: `lines` 里**来自 wire 历史**那一段「产生时的形态」
                (`visible_transcript` 的产出) —— 编号与修订都以它为准 (只有这几条有
                wire 下标); None 表示这一批一条都不是从 wire 历史来的.
        """
        if run_id is None:
            # `begin` 没成 (没配记录层时压根不会走到这里): 本轮不记账. 不在这儿
            # 补建行 —— 帧上的编号已经定了, 事后补的行对不上它们; 也**不重复记
            # 日志**: 那件事 `begin` 已经说过一次了 (同一轮报两条只会稀释信号).
            # 只留「这段会话欠着一条提示行」这个标记, 由下一次成功写入补上
            self._missed.add(thread_id)
            return False
        # 提示行可能被补在这批行的**前面** (见 `_pending`): 编号按位置算, 所以先
        # 知道补了几条.
        #
        # 编号只有**来自 wire 历史**的那几条才派生的 (`produced` 给出的就是那一段, 与
        # `lines` 的前几条一一对应); 补进来的内部件 (提示行 / 摘要 / 「这一轮没答完」
        # 那句说明) **没有 wire 下标** —— 给随机编号. 混着算会出事: 说明行拿到的
        # 编号可能与这一轮真产生的某条消息撞上, 于是幂等写把它当「已经写过了」跳过,
        # 而用户看不到「这里少了一轮」那句话
        batch = self._pending(thread_id, lines)
        lead = len(batch) - len(lines)
        from_wire = len(produced) if produced is not None else 0
        ids = [uuid4().hex] * lead + [
            message_id_for(run_id, since + offset)
            if offset < from_wire
            else uuid4().hex
            for offset in range(len(lines))
        ]
        try:
            await self._ensure_thread(thread_id, title_for(lines))
            # 运行行那一笔 (六步里的第二步): 这一段不收尾 (settlement=None, HITL 的
            # 续跑段) 就跳过它 —— 那一行横跨挂起等待期, 该写什么由收尾那段说;
            # 下面那几步照走 (它们记的是这一段**产生的事实**, 与结没结账无关)
            if settlement is not None:
                await self._runs.settle(
                    run_id,
                    status=settlement.status,
                    last_checkpoint_id=settlement.last_checkpoint_id,
                    **asdict(settlement.facts),
                    # 金额只在算得出来时写 (仓储那边也只在这时候动那一列): 算不出来
                    # 就留着 NULL —— 「没算出来」不是「没花钱」, 更不是把已有的金额
                    # 清掉. 明细两种情形都写: 它是「这笔钱怎么来的」或「为什么没有」
                    total_cost=settlement.cost.total if settlement.cost.known else None,
                    total_cost_detail=settlement.cost.to_detail(),
                )
            # 消息行带上 run_id: 它们确实属于这次执行 —— 审计时「这几条是哪一次
            # 问答产生的」靠它 (列注释: NULL 表示不是 agent 跑出来的)
            #
            # 提示行与本轮那几行**同一批**插进去: 要么都成, 要么都不成 ——
            # 否则会出现「说了缺一轮, 但本轮也没写进去」这种更乱的中间态.
            # 这一批是**补齐** (运行中各拍已经写过的不再写第二遍), 而编号算得出来
            # 正是为了这个: 两次写落在同一行上
            await self._messages.add_lines(
                thread_id=thread_id,
                lines=batch,
                run_id=run_id,
                ids=ids,
            )
            await self._revise(
                run_id=run_id, lines=lines, produced=produced, since=since
            )
            await self._write_calls(run_id=run_id, calls=calls)
            await self._threads.touch(thread_id)
        except DbError as exc:
            self._missed.add(thread_id)
            logger.warning(
                "会话 %s 这一轮没能记进记录表: %s", thread_id, exc, exc_info=True
            )
            return False
        self._missed.discard(thread_id)
        return True

    async def _revise(
        self,
        *,
        run_id: str,
        lines: Sequence[TranscriptLine],
        produced: Sequence[TranscriptLine] | None,
        since: int,
    ) -> None:
        """把「产生时那一拍写下的形态」改成这一轮的**最终形态** (只动不一样的那几条).

        什么时候真的会有差别: 截断续写 (CONTINUE) 把**一次答复**拆成好几段 assistant
        消息 —— 运行中那几拍如实各写一行 (每一条都是「不带工具调用的 assistant」,
        于是都可见), 而到了收尾才知道它们合成一条之后的样子: 最后一段拿拼合好的完整
        正文 (**取 `LoopResult.content`**, 不从消息数组末尾抄), 早先那几段退成隐藏行.
        不修订的话, 同一段答案在记录里会成两截先后出现 (`db/conversation.py` 的模块
        docstring 讲的就是这件事).

        比较的基准是 `produced` (按 `visible_transcript` 算出的「产生时形态」): 一样
        就不发 UPDATE —— 修订是**例外**, 不该每轮都往库里写一遍.
        """
        if not produced:
            return
        for offset, (target, written) in enumerate(zip(lines, produced, strict=False)):
            if target == written:
                continue
            await self._messages.update_line(
                message_id_for(run_id, since + offset),
                content=target.content,
                reasoning=target.reasoning,
                hidden=target.hidden,
            )

    async def _write_calls(self, *, run_id: str, calls: Sequence[ToolCallFact]) -> None:
        """写 / 推进这一批工具调用行: 先按 `pending` 建行, 再把有结论的推进到终态.

        两笔与 issue 22 在 `runs` 上建立的 begin/finish 同构, 兑现的是 ticket 27 定下
        的那条时机: **执行前落 pending、执行后回填结果**. 两笔都幂等 (建行跳过已有的、
        推进把同一个结论再写一遍), 于是「哪一拍没写上」由收尾那一趟补齐, 不必记住
        差了什么.

        为什么每条都先建 `pending` 行 (而不是一笔写成结论): `needs_approval` (挂起等人)
        与 `succeeded` 都是**结论**, 而结论不该是这一行的第一个状态 —— 第一个状态是
        「模型刚发起」(见 `ToolCallStatus` 的注释). 代价是每条多一次 UPDATE; 一个 run
        的调用只有几条到几十条, 换来的是**挂起那条与普通那条走同一个形状** (两套写法
        必然漂移).

        归属靠 `fact.message_index` 算 (`message_id_for`), 于是**同一次运行的第二段**
        (审批恢复) 能直接寻址第一段写下的那一行.

        反过来说: 若补做的那条调用, 发起它的 assistant 行不属于**本次** run (命令行
        `--resume` 开的是新的一次运行, 而快照里那条 assistant 行是上一次运行写的),
        这个编号在本次 run 里没有对应的消息行 —— 外键会拦下, 降级成一条 warning.
        **这条路 issue 33 定了, 走的是上面那条**: HITL 的续跑沿用挂起那次运行的编号
        (本来就是同一次运行), 于是补做的调用直接寻址第一段写下的那一行. 剩下那半边
        (命令行从**挂起点**续跑) 今天到不了: 挂起点只由 HITL 产生 (issue 34), 而命令行
        那条是「接着上次聊」. 真到了那一天, 修法在这里已经记下 —— 按帧里的 `run_id`
        反查那条 assistant 行的编号, 或把它按本次运行补写一行.
        """
        if not calls:
            return
        await self._calls.add_calls(
            run_id=run_id,
            calls=[
                build_tool_call(
                    run_id=run_id,
                    # 发起它的那条 assistant 消息: 编号由 (run_id, 下标) 算出来 ——
                    # 运行中那一拍写的是同一个编号, 所以这里写的是**同一行**
                    message_id=message_id_for(run_id, fact.message_index),
                    tool_call_id=fact.tool_call_id,
                    tool_name=fact.tool_name,
                    arguments=fact.arguments,
                    status=ToolCallStatus.PENDING,
                )
                for fact in calls
            ],
        )
        for fact in calls:
            if fact.outcome is ToolCallOutcome.PENDING:
                # 还没执行 (执行前那一拍): 这一行停在「模型刚发起」就是事实
                continue
            await self._calls.set_status(
                run_id,
                message_id_for(run_id, fact.message_index),
                fact.tool_call_id,
                tool_call_status_for_outcome(fact.outcome),
                result=fact.result,
                duration_ms=fact.duration_ms,
                # 要人批的那一条把「问什么」带上 (挂起那一刻就落库): 刷新页面之后
                # 前端靠这两列重建确认卡, 而**只能在这一笔写** —— 上面建行那一拍
                # 不知道要不要人批 (裁决还没发生), 而建行走的是幂等插入 (已有的跳过)
                approval_prompt=fact.approval_prompt or None,
                approval_needs=fact.approval_needs or None,
            )

    async def _ensure_thread(self, thread_id: str, title: str) -> None:
        """会话行懒创建: 有就复用 (顺手补个空标题), 没有就用 title 建一个.

        两拍都带着标题 (`begin` 拿的是这次提问, 收尾那拍拿的是这批行里第一条用户
        消息) —— 于是第一次开口就建出带标题的会话行. **补空标题是为 `begin` 没给
        标题的那种调用** (protocol 里 `title` 的默认值是空串): 不补的话, 那段会话
        在前端左栏里永远没有名字 (而它明明聊过). 只补空的, 已有标题不覆盖 ——
        标题的语义是「首条用户消息」, 不该漂.

        `get` 再 `add` 之间有一个窗口, 但框架的部署纪律本就是**单进程**
        (`server/sessions.py` 写明), 而同一段会话又不许并发 (登记表的 busy 集合
        拦着) —— 于是这两步之间不会有第二个写入者.

        Args:
            thread_id: 哪段会话.
            title: 首条用户消息规范化后的标题; 空串表示这批行里没有用户消息.
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
                return
            if not existing.title and title:
                await self._threads.set_title(thread_id, title)
            return
        await self._threads.add(
            thread_id=thread_id,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            title=title,
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


def normalize_title(text: str) -> str:
    """标题文本 → 存库那一行: 压平空白 + 截断.

    为什么要压平空白: 标题在前端是**一行**, 而用户的提问可能带换行 (粘贴一段
    报错、写几条要点) —— 原样存进去会把列表那一行撑成好几行.

    单独一个函数 (而不只藏在 `title_for` 里): `begin` 那一步还没有「行」,
    只有这次提问的原文, 它要的是同一条规范化规则.
    """
    return " ".join(text.split())[:TITLE_LIMIT]


def title_for(lines: list[TranscriptLine]) -> str:
    """会话标题: 这批行里第一条用户消息 (规范化后).

    Args:
        lines: 要落库的那批行 (顺序即时间顺序).

    Returns:
        str: 标题; 这批行里没有用户消息时是空串 (``add`` 的约定: 空串表示还没起名).
    """
    for line in lines:
        if line.role == "user" and line.content:
            return normalize_title(line.content)
    return ""


__all__ = [
    "MISSED_TURN_TEXT",
    "TITLE_LIMIT",
    "UNFINISHED_TURN_TEXT",
    "ConversationRecorder",
    "RunFacts",
    "RunRecorder",
    "RunSettlement",
    "normalize_title",
    "title_for",
]
