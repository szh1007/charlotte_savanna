"""checkpoint 包的数据形状: 快照长什么样、里面装了什么 (difficulties #5).

一句话理解: 快照 (Checkpoint) 就像游戏的**存档** —— 把某一刻的对话原样抄一份
存起来, 以后可以读回来接着玩. 本文件定义的就是「存档」以及「存档里装的东西」
长什么样.

四样东西, 从里到外:
- CheckpointState     存档里装的**进度**: 对话历史 + 计数器 + 挂起点 —— 全部是
                      「接着跑所必需」的输入. 恢复只看它.
- CheckpointMetadata  存档的**观察值**: 这一帧是怎么产生的 (正常一轮 / 从快照
                      分叉 / 补做挂起的调用)、这一轮花了多少 token 与时间、调了
                      哪些工具、答了什么、为什么停. 给人和调试看, 恢复不需要它.
- Checkpoint          一条**存档记录**: 元数据 (编号 / 会话 / 运行 / 轮次 / 版本 /
                      父帧 / 时刻) + 进度 + 观察值.
- Suspension          卡住的地方: 为什么停下 + 还欠哪几条工具调用没做 (#25 HITL).

进度与观察值为什么要分开 (对齐 LangGraph 的 checkpoint / metadata 两分):
1. 恢复只需要进度 —— 观察值不进恢复路径, 少一处能读歪的地方
2. 观察值能单独查/单独建索引 (Postgres 里它是单独一列): 「这个会话里哪些帧是从
   分叉产生的」这类调试问题一条 SQL 就能问

为什么单独放一个文件: 三个存储实现 (内存 / Redis / Postgres) 与序列化协议都要
用同一套形状 —— 形状定在这里, 谁都不许自己发明字段, 否则「换个存储就恢复不出
同样的结果」, 三种存储之间的对比就无从谈起.

不存什么 (有意取舍): 逐轮的 TurnRecord 与模型原始响应不存. 快照存的是「接着跑
需要什么 + 这一步发生了什么」, 不是运行流水账 —— messages 里已经含有每一轮说了
什么、工具返回了什么; 逐轮明细那种「全过程录像」属 Trace (P2 #39/#59).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from CharAgent.checkpoint.utils.errors import CheckpointConfigError
from CharAgent.model.utils.types import ModelMessage, ModelToolCall

SCHEMA_VERSION = 7
"""当前快照格式的版本号 (difficulties #5 向前兼容).

改字段结构的**不兼容**变化就 +1, 并在 utils/migrations.py 写一个「老版本 → 新
版本」的翻译函数; 只读代码的人凭这个数字知道老快照还能不能读回来. 版本历史:

- v1: 只有消息列表 (state 就是裸列表, 早期原型)
- v2: state 变成结构化字典 (messages + 计数器 + 正文片段 + 结束原因)
- v3: 观察值从 state 搬进 metadata (content / finish_reason / outcome),
  并新增 source / turn_tokens / turn_elapsed_ms / tool_names 四个调试字段
- v4: 进度多两样「上下文压缩」的账 —— summary (摘要正文) 与
  summary_covers (它压到第几条). 老帧没有摘要, 补 None / 0 = 「那时候还没压过」
- v5: 两件事 —— ① **身份说明不再逐帧抄正文**, 改存一个引用 (prompt_ref:
  名字 + 渲染后正文的 sha256, 见 prompt/ref.py), 正文的唯一定义仍在提示词目录里;
  ② 累计用量多出五个**归因**字段 (input / output / reasoning / cache_hit /
  cache_miss), 它们是 total_tokens 的分解, 供成本归因 (#34) 拆解用. 老帧两样都
  没有: prompt_ref 补 None —— 正文仍在 messages[0] 里, 那正是当时的事实, 不是
  「补不上」; 五个计数器补 None —— 「上游一次都没上报过」, 与 0 是两回事
- v6: **两个 `run_id` 各归其位** (ticket 22) —— 帧上那个「哪一次**循环
  执行**落下的」更名 `loop_id` (存储键一起改; 老帧里的 `run_id` 键按它读), 同时
  新增一个 `run_id` 指向**记录层**的运行行 (`charagent_runs.run_id`, 可空: 没配
  记录层的进程就是 None). 老帧补 `run_id=None` —— 「这一帧不属于任何一行账」,
  与「指向某一行」是两回事
- v7 (当前): **视图那份也走引用** (ticket 26) —— `metadata.view` 里的身份说明
  同样摘出来存 `prompt_ref` (与进度那边同构), 于是那几千字在帧里只出现一次.
  老帧的 `view.messages` **带**着身份说明 (那时候没摘), 补 `prompt_ref=None`:
  与 v5 对进度那条同一个意思 —— 「这一帧的这份没剥离过, 正文就在 messages 里」,
  于是老帧仍然完全自描述
"""

# 标识别符 (thread_id / loop_id) 的长度上限与禁用字符 (见 check_identifier)
_IDENTIFIER_MAX_LEN = 128


def check_identifier(name: str, value: str) -> str:
    """校验 thread_id / loop_id: 非空、不太长、不带空白与控制字符.

    为什么要卡这一关 (与 IdempotencyKey 同一条理由 #17): 这两个值会变成存储里
    的**键名** (Redis 的 key)、**主键与索引** (Postgres 的列) 和**日志字段**.
    空白、通配符、换行这类字符混进去, 轻则难查 (日志里看不出边界), 重则让键
    串味 (别人拼错键名还能撞上). 挡在入口最省事.

    公开的理由: 除了造快照 (Checkpoint.create 会调它), AgentLoop 在**构造期**
    也要拿它查一次 thread_id —— 配置写错该在装配时就报, 而不是跑到第一帧落盘
    才炸 (那时已经在处理用户请求了).

    Args:
        name: 字段名 (报错信息里用, 如 "thread_id").
        value: 待校验的值.

    Returns:
        str: 校验通过的原值 (方便链式使用).

    Raises:
        CheckpointConfigError: 不是字符串 / 空串 / 超长 / 含空白或控制字符.
    """
    if not isinstance(value, str) or not value:
        raise CheckpointConfigError(f"{name} 必须是非空字符串, 实际: {value!r}")
    if len(value) > _IDENTIFIER_MAX_LEN:
        raise CheckpointConfigError(
            f"{name} 过长 ({len(value)} 字符, 上限 {_IDENTIFIER_MAX_LEN}): "
            f"{value[:32]!r}..."
        )
    if any(char.isspace() or not char.isprintable() for char in value):
        raise CheckpointConfigError(f"{name} 不能含空白或控制字符: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class CheckpointCapabilities:
    """一个存储实现「能做什么」的声明 (存储介质不同, 能力也不同).

    attributes:
        history: 能不能翻出**历史**快照 (存了很多帧, 可按 id 取回、按时间列出).
            True = 内存 / Postgres / Redis 的 history 模式; False = Redis 的
            latest 模式 (一个会话只留最新一帧, 存新的就把旧的盖掉).
        ttl: 快照会不会**自己过期消失** (Redis 会给键设过期时间, 到点自动删).
    """

    history: bool
    ttl: bool


class CheckpointSource(StrEnum):
    """一帧快照是怎么产生的 (回放调试时一眼看出「这一步是哪儿来的」).

    存的是字符串取值 (不是枚举对象): 快照要长期留在存储里, 枚举取值才是跨版本
    稳定契约 —— 以后枚举改名或加成员, 老快照照样读得回来.
    """

    LOOP = "loop"  # 本次运行正常跑出来的一轮
    FORK = "fork"  # 本次运行是从一帧快照接着跑的, 这是它落下的第一帧
    SUSPENSION = "suspension"  # 补做挂起时欠下的工具调用, 因此落下的那一帧


@dataclass(slots=True)
class Suspension:
    """卡住的地方: 这次运行停下来了, 还欠着几条工具调用没做完 (#25 HITL).

    例子: 用户要退款, 模型决定调用退款工具; 退款是危险动作, 得人工点「同意」
    才能做 —— 于是先把当前进度 (含那条还没执行的退款调用) 存成快照, 等人批准.
    批准后从快照接着跑, **只补做那一条**, 前面的轮次一步都不重跑. 这就是「挂起
    点快照」的意义: 恢复靠存档, 不靠重来.

    attributes:
        reason: 为什么停下 (短标签, 如 "needs_approval"; 面向机器判断, 不是给
            用户看的文案).
        pending: 还欠结果的工具调用, 顺序即当初模型给出的顺序 (原样保留, 恢复
            时按这个顺序补做).
        approval_id: 审批单号 (审批记录 id). P0 允许为 None —— 机制先备
            好, 审批表与本字段的接线留待后续阶段.
    """

    reason: str
    pending: list[ModelToolCall] = field(default_factory=list)
    approval_id: str | None = None


@dataclass(slots=True)
class CheckpointState:
    """一份快照里装的**进度** (接着跑所需的全部信息; 恢复只看这个对象).

    attributes:
        messages: 完整 wire 消息历史 (含每轮的 assistant 与 tool 结果). 恢复时
            直接把它当新一轮 run 的输入即可 —— 已经做完的事都在里面, 所以不会
            重做.
        turn_count: 已经跑完几轮 (恢复后从它接着数, 轮数不会从 1 重来).
        total_tokens: 累计 token (恢复后接着累计, 于是「整个 run 的预算」跨断点
            仍然算数, 见 agent/guard.py 的 token 预算).
        truncation_count: 已经处理过几次 length 截断 (截断重试上限也跨断点算数).
        content_parts: 截断续写 (CONTINUE 策略) 已写出的正文片段 —— 接着跑时
            要和后面写出来的段落拼在一起, 才是一份完整答复.
        suspension: 卡在等人批准的地方; None 表示没卡 (正常跑完或跑一半).
        summary / summary_covers: 上下文压缩 (#7, v4 起) 的进度 —— 当前生效的
            摘要正文, 以及它覆盖到 messages 的第几条. 它们是**接着压**要用的
            输入 (滚动摘要要连上一条一起重压), 所以属进度而不是观察值. 老帧
            (v3 及更早) 缺这两个字段, 补 None / 0 = 「那时候还没压过」.
            注意 messages 存的是**全量账本** (压缩不落地), 摘要只是送模型那份
            视图里的替身.
        prompt_ref: 身份说明的**引用** (v5 起): `{"name": 提示词名, "sha256":
            渲染后正文的哈希}`, 见 prompt/ref.py. 非空表示「这一帧的 messages
            **不含**身份说明那条, 它得由读的人按引用补回来」; None 表示正文就
            内联在 messages[0] 里 (v4 及更早的帧, 或调用方根本没给引用).
            为什么可以不存正文: 同一段会话的每一帧抄的都是同一份几千字配置,
            而正文的唯一定义在盘上按版本留着 (一版一个文件) —— 帧里记「是哪一份」
            就够了. **读回来时必须还原** (prompt.ref.restore_identity), 否则模型
            拿到的请求就少了一条身份说明.
        input_tokens / output_tokens / reasoning_tokens / cache_hit_tokens /
        cache_miss_tokens: 累计用量的**分解** (v5 起) —— 与 total_tokens 同一个
            口径的五个分量 (输入 / 输出 / 思维链 / 缓存命中输入 / 缓存未命中输入).
            total_tokens 是账单原值, 这五个是它的归因拆解 (#34): 「钱花在哪一类
            token 上」靠它们才答得出来 (缓存命中的单价远低于未命中, 两者混在一个
            总数里看不出区别). **None 表示上游一次都没上报过这个分量**, 与 0
            (报过、值就是零) 是两回事 —— 写 0 会让「不适用」看起来像「真的没命中」.
            续跑时接着累计 (与 total_tokens 同规矩), 否则断点续跑的 run 会出现
            「总量有值、分解全是 0」这种自相矛盾的行.

    注意这里**没有** content / finish_reason / outcome: 那三个是「当时答成什么
    样、为什么停」的观察值, 属 CheckpointMetadata (v3 起从 state 搬过去).
    """

    messages: list[ModelMessage]
    turn_count: int = 0
    total_tokens: int = 0
    truncation_count: int = 0
    content_parts: list[str] = field(default_factory=list)
    suspension: Suspension | None = None
    summary: str | None = None
    summary_covers: int = 0
    prompt_ref: dict[str, str] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None


@dataclass(slots=True)
class CheckpointMetadata:
    """一帧快照的**观察值** (给人和调试看; 恢复不需要它).

    attributes:
        source: 这一帧怎么产生的, 见 CheckpointSource.
        turn_tokens: 这一轮新增的 token (状态里的 total_tokens 是累计值, 想知道
            「哪一步最贵」看这个).
        turn_elapsed_ms: 这一轮花了多少毫秒 (同上, 累计耗时不便比较).
        tool_names: 这一轮调用了哪些工具 (按模型给出的顺序; 名字重复说明同一轮里
            同名工具被调了多次).
        content: 那一刻的答复正文 (没答完时为 None). 属观察值 —— 恢复不靠它, 本次
            run 最终答复以 LoopResult.content 为准.
        finish_reason / outcome: 最后一次响应为什么停 / 这次 run 为什么结束
            (None 表示当时还没跑完). 两者都存**字符串**而不是枚举: 快照要长期留
            在硬盘上, 枚举的取值 (如 "finished") 才是稳定契约.
        view: 这一轮**真的发出去**的那份 (上下文视图, ticket 22 第 4 件; 由
            `agent/compaction.py` 的 `view_payload` 装). 压缩把「账本」与「送给
            模型的」分成两份, 而帧里原本只有账本 —— 「当时它看到了什么」于是答不
            上来. 取值见那个函数: `messages` 只在视图**不等于**账本时才带 (None =
            视图就是账本本身, 不必再抄一份). None = 这一轮没有模型调用 (挂起补做
            那一轮) 或压根没配压缩策略.
    """

    source: CheckpointSource = CheckpointSource.LOOP
    turn_tokens: int = 0
    turn_elapsed_ms: float = 0.0
    tool_names: list[str] = field(default_factory=list)
    content: str | None = None
    finish_reason: str | None = None
    outcome: str | None = None
    view: dict[str, Any] | None = None


@dataclass(slots=True)
class Checkpoint:
    """一条存档记录: 身份 + 位置 + 从哪来 + 进度 + 观察值 (字段见下面的 attributes).

    attributes:
        checkpoint_id: 本条存档的编号 (uuid4 hex, 全局唯一).
        thread_id: 属于哪一段对话 —— 一个对话的所有存档排成一条线 (分区键).
        loop_id: 是哪一次**循环执行**存下的 (loop 自己生成, 一个 run 一个; 续跑
            默认沿用原 `loop_id` = 「同一个 run 接着跑」, 想另起一次就显式传新的,
            见 agent/loop.py 的 resume). **一次循环执行的几帧共享同一个 `loop_id`**
            (每 turn 一帧, `turn_number` 递增).
        run_id: 这一帧属于**记录层**的哪一行账 (`charagent_runs.run_id` 的外键);
            None = 不属于任何一行 —— 没配记录层的进程 (框架 CLI 演示)、那一轮没记
            上账、老帧, 三种都是它. 2026-09-23 (ticket 22) 之前这个位置放的是
            `loop_id`, 两个 `run_id` 撞名且对不上, 于是改名 + 新增.
        turn_number: 存下它的时候, 已经跑完几轮 (「执行到哪一步」#5).
        state: 进度 (见 CheckpointState).
        metadata: 观察值 (见 CheckpointMetadata).
        parent_id: 上一份存档的编号; None 表示这是这条线的头一份. **从老快照
            恢复会产生新分支**: 新存档的 parent_id 指向那份老快照, 于是历史
            长成一棵树, time-travel 的回溯路径一目了然 (#5).
        created_at: 存下的时刻 (带时区; 排序与回溯都看它).
        schema_version: 本记录的格式版本, 恒等于当前版本 SCHEMA_VERSION ——
            老快照读进来时已经升级过了 (存储里那份老文本的版本号另说, 见
            serialization.py 的 decode_record).
    """

    checkpoint_id: str
    thread_id: str
    loop_id: str
    turn_number: int
    state: CheckpointState
    metadata: CheckpointMetadata = field(default_factory=CheckpointMetadata)
    # 记录层那一行的编号 (外键); None = 这一帧不属于任何一行账 (见 attributes)
    run_id: str | None = None
    parent_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        thread_id: str,
        loop_id: str,
        turn_number: int,
        state: CheckpointState,
        metadata: CheckpointMetadata | None = None,
        run_id: str | None = None,
        parent_id: str | None = None,
        checkpoint_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Checkpoint:
        """造一条新快照: 编号与时刻在这里统一生成 (别处不要自己拼 uuid / now).

        编号与时间**可显式传入**: 测试要的是「同样的输入 → 一模一样的结果」
        (#61 确定性), 随机编号和当前时间会让快照对比失败.

        Args:
            thread_id / loop_id: 会话与循环执行标识 (会被校验, 见 check_identifier).
            turn_number: 已跑完的轮数 (0 表示还没跑过).
            state: 进度.
            metadata: 观察值; None 表示按「普通一帧」记 (source=loop, 其余默认).
            run_id: 记录层那一行的编号; None = 这一帧不属于任何一行账 (没配记录层 /
                那一轮没记上 / 老帧). **不校验**: 它是别人发的编号, 校验规则在记录
                层那边 (这里只当一个不透明的值原样带上).
            parent_id: 上一份存档的编号 (线性续存传它; 新会话传 None).
            checkpoint_id: 显式编号 (测试用; None 表示生成 uuid4 hex).
            created_at: 显式时刻 (测试用; None 表示现在, UTC).

        Returns:
            Checkpoint: 新快照 (schema_version 恒为当前版本).

        Raises:
            CheckpointConfigError: 标识符不合法, 或轮数为负.
        """
        if turn_number < 0:
            raise CheckpointConfigError(f"turn_number 不能为负, 实际: {turn_number}")
        return cls(
            checkpoint_id=checkpoint_id or uuid4().hex,
            thread_id=check_identifier("thread_id", thread_id),
            loop_id=check_identifier("loop_id", loop_id),
            turn_number=turn_number,
            state=state,
            metadata=metadata if metadata is not None else CheckpointMetadata(),
            run_id=run_id,
            parent_id=parent_id,
            created_at=created_at or datetime.now(UTC),
        )
