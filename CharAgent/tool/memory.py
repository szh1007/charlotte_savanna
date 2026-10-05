"""长期记忆的三个工具: `remember` / `recall` / `forget` (difficulties #31-#33, C13/C30).

一句话理解: 把 `db/` 那层记忆仓储 (C12/C30) 的能力翻成**模型的几个动作** ——
「这件事以后还用得上, 记下来」「这个用户以前说过什么, 想起来」「这一条不要了,
忘掉」. 都走工具 (而不是在每轮开头把记忆注入上下文): 模型自己决定什么时候查、
要不要写, 写什么也由它提炼 —— 这正是 #33 挑的那条路, 理由是「规则抽不出『以后
还用得上』这句话」, 而工具形态还能在轨迹里**看见**模型主动记了一笔.

| 工具 | 形状 | 什么时候调 |
|------|------|-----------|
| `remember(content, kind)` | 写一条 (替换型顶旧值) | 模型发现「跨会话还有用」时 |
| `recall()` | 取回活记忆, 每条带编号 | 模型需要「他以前说过什么」时 |
| `forget(memory_id)` | 忘掉一条 (编号来自 recall) | 用户说「忘掉…」时 |

**身份不进参数表** (与业务工具同一条纪律, CharAgent 这边对齐 `CharApp` 的约定
①⑤): `tenant_id` / `user_id` 由装配处用闭包裹住 —— 模型既看不见也改不了
「记给谁 / 读谁的」. 本模块因此一个身份参数都没有, 有一条 schema 用例守着.

**两种 kind 行为** (C30): 累积型 (`episodic` / `semantic`) 多条并存、只增;
替换型 (`style` / `nickname`) 同一时刻只留一条 —— 写新的把旧的顶掉 (仓储层
自动软删, **模型不需要知道「要先删再写」**: 改了主意直接记新的即可, 这正是
「不要因为实现细节多给模型加工序」). 行为表在 `db/entities.py` 的
`KIND_BEHAVIORS`.

**写入记来源, 取回记使用** (C30): `remember` 落库时带 `source_thread_id`
(装配处经闭包给)与 `source_run_id` (执行期从日志上下文读 —— 记录层那一行的
编号, 见 `structured_logging.current_ids`); `recall` 取回后把这几条的
`last_used_at` 刷成当下 (只记账, **不进排序** —— 现在全量返回没有区分度,
等召回变子集那天再谈权重, 见 C13 的遗留记录).

**「谁决定写」是模型自己** (#33 的取舍): 比规则抽取多一样东西 —— 模型知道
「这句话以后还用得上」. 代价 (写多了污染 / 写少了没用) 的缓解靠**仓储那一层**
的容量上限 + 衰减淘汰 (C12), 不靠 prompt 求它节制 —— 本模块因此不做写入频率
限制, 只做**内容级**的一道闸: 敏感值拒写.

**敏感值拒写, 不是打码** (与 `redact/` 的分工): 那四条规则在这里被复用
(`MASK_RULES` 的正则), 但语义相反 —— `redact/` 是「打码之后照写」(日志要留痕),
这里是「拒绝写入」(记忆会在以后每一段对话里**喂给模型读**, 敏感值不该长期留在
里面). 判据的形状 = redact 认得的四条 (手机号 / 邮箱 / 证件号 / 银行卡) +
「一次性密码」那条 (6 位数字: 支付密码与验证码都是这个形状).

**`recall` 不带 `query`** (#33 明说的边界): 条数有上限 (仓储的容量淘汰把每个
用户压在 N 条以内), 全部按衰减排序返回就够 —— 带 `query` 就需要向量或关键词
匹配, 而那是**记忆量大到「列不完」时**才成立的优化. 什么时候该加: 单个用户的
记忆条数逼近上限、全量返回开始挤占上下文时.

**`forget` 按编号删, 不做内容模糊匹配** (C30): 编号是 `memory_id` 的前 8 位
(recall 每条显示); 模型从列表里**自己判断**删哪条, 系统零猜测 —— 模糊匹配
需要相似度判断, 判错就是删错行, 代价远大于让模型多看一眼编号.

**这一层的失败不吞**: 仓储抛 (库连不上) 就让它走框架统一的内部错误文案 (与
业务工具的外部故障同一条路) —— 记忆是**增强**不是依赖, 但它坏了不能假装没坏.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field

from CharAgent.db.entities import KIND_BEHAVIORS, MemoryKind
from CharAgent.db.repositories.memories import MemoriesRepository
from CharAgent.redact import MASK_RULES
from CharAgent.structured_logging import current_ids
from CharAgent.tool.decorator import Tool, tool
from CharAgent.tool.utils.errors import ToolActionableError

# 一条记忆的长度上限 (字符). 「一句话事实」的尺度: 长过这个数的东西多半是原文
# 或摘要 —— 那两样都不该进记忆表 (见 C12 的 MEM-D2), 该由模型先提炼.
MAX_CONTENT_LENGTH = 200

# recall 里展示的编号长度 (memory_id 的前几位十六进制). forget 按它前缀匹配 ——
# 8 位在「一个用户至多几十条」的规模下碰撞概率可忽略, 而比 32 位整串好抄得多
# (工具层另有「命中多条就拒绝」的兜底, 见 forget).
ID_DISPLAY_LENGTH = 8

# 拒写的形状: redact 认得的四条 (**复用它的正则**, 免得同一把尺子抄两处) +
# 一次性密码那条. 6 位数字前后也一样用 `(?<!\d)` / `(?!\d)` 顾盼 —— 中文里
# `\b` 不可靠 (汉字在 re 里也算 \w, 见 redact/rules.py 的第 2 条纪律), 而这两
# 组边界缺一不可: 少了它, 一个 24 位订单号会被拆出一个 6 位子串误报.
SENSITIVE_PATTERNS: dict[str, re.Pattern[str]] = {
    **{name: mask.pattern for name, mask in MASK_RULES.items()},
    "one_time_code": re.compile(r"(?<!\d)\d{6}(?!\d)"),
}

# 命中形状 → 拒绝文本里点名的中文说法 (给模型看的: 它据此判断该怎么跟用户说)
_SHAPE_LABELS: dict[str, str] = {
    "id_card": "证件号",
    "bank_card": "银行卡",
    "phone": "手机号",
    "email": "邮箱",
    "one_time_code": "一次性密码 / 验证码",
}

# 拒写时回给模型的文本. 三个要点缺一不可 (与业务侧 `_NO_AUTHORIZATION_TEXT`
# 同一套写法): 说清**没写** (别让它以为记上了)、给出替代做法 (现查)、劝住重试
# (换十个说法还是这条内容, 重试只是白烧一轮).
_REJECTED_TEXT = (
    "这条没有写进记忆: 内容里含疑似{shape}. 长期记忆不保存手机号 / 邮箱 / 证件号 / "
    "银行卡 / 一次性密码这类值 —— 需要它们时用对应的工具现查 (账户资料与订单里都"
    "有). 不要重试同一条, 也不必向用户解释细节."
)


def _sensitive_shape(content: str) -> str | None:
    """这段内容里有没有疑似敏感值; 有则回它的中文说法, 没有回 None."""
    for name, pattern in SENSITIVE_PATTERNS.items():
        if pattern.search(content):
            return _SHAPE_LABELS[name]
    return None


def build_memory_tools(
    repository: MemoriesRepository,
    *,
    tenant_id: str,
    user_id: str,
    thread_id: str,
) -> list[Tool]:
    """返回 [remember, recall, forget] 三个工具, 身份由闭包持有.

    Args:
        repository: 记忆仓储 (C12/C30). 只要形状对 (`add` / `list_for_user` /
            `list_by_id_prefix` / `touch_used` / `soft_delete`), 测试替身也能传
            —— 这与框架其余部分同一条纪律: 结构化的协议, 不继承.
        tenant_id / user_id: 这段记忆记给谁 / 读谁的 / 删谁的 —— 装配处从运行
            上下文取 (与会话隔离同一对键: 「谁能读到谁的记忆」与「谁能看到谁的
            会话」是同一条边界).
        thread_id: 这串记忆写在**哪段对话**里 (C30) —— 装配处从运行上下文取.
            它和 tenant/user 同族 (装配期就定的事实), 所以走闭包; 而运行编号
            (`source_run_id`) 到执行期才存在, 从日志上下文读 (见 `remember`).
    """

    @tool
    async def remember(
        content: Annotated[
            str,
            Field(
                description="一句能独立看懂的事实 (按用户的说法提炼, 不要带上"
                "「他说」这类转述), 例如「偏好货到付款」「上次说他换了工作」",
                min_length=1,
                max_length=MAX_CONTENT_LENGTH,
                examples=["偏好货到付款", "上次说他换工作了"],
            ),
        ],
        kind: Annotated[
            Literal["episodic", "semantic", "style", "nickname"],
            Field(
                description="记忆种类: semantic = 世界事实与业务偏好 (「以后寄到"
                "公司」); episodic = 发生过的一件事 (「上周申请过一次退款」); "
                "style = 对你怎么回答的偏好 (语气 / 长短 / 语言); nickname = "
                "希望你怎称呼他. style 与 nickname 是**替换型** —— 同一时刻只留"
                "最新一条, 他改了主意就直接记新的, 旧的自动失效"
            ),
        ],
    ) -> str:
        """把一件**跨对话以后还用得上**的事记进长期记忆. 用户说了一个偏好
        (「以后都寄到公司」)、一个长期事实 (「对花生过敏」)、一件发生过的事
        (「上周申请过一次退款」), 或对你怎么回答 / 怎么称呼他有了要求时使用;
        用户明说「记住这个」时也用. **不要记**: 这一轮查到的订单号 / 余额 /
        验证码 (下次自己查得到)、用户当前问题的临时上下文 (「这次简短点」不算
        长期偏好)、你自己的推测 (只记他亲口说的), 以及手机号 / 邮箱 / 证件号 /
        银行卡 / 一次性密码这类敏感值 (工具会拒, 别试). 一句话一件事; 记过了的
        内容再记一次不会产生两条.
        """
        shape = _sensitive_shape(content)
        if shape is not None:
            raise ToolActionableError(_REJECTED_TEXT.format(shape=shape))
        memory = await repository.add(
            tenant_id=tenant_id,
            user_id=user_id,
            content=content,
            kind=MemoryKind(kind),
            source_thread_id=thread_id,
            source_run_id=current_ids().run_id,
        )
        return f"已记住: 「{memory.content}」"

    @tool
    async def recall() -> str:
        """取回你为**当前用户**记住的事 (新的在前, 每条带种类、时间与编号).
        **每段对话的开头**, 这一问可能与他以往的偏好或说过的事有关时使用: 先调
        一次它看看 —— 一次就够, 不要每一轮都调; 记忆是空的时候就照常回答. 想起
        的风格 / 称呼就照它来; 要忘掉某一条时, 从这里的编号里挑. 不要用它查别的
        东西: 实时数据 (订单 / 余额 / 库存) 用各自的工具查.
        """
        rows = await repository.list_for_user(tenant_id, user_id)
        if rows:
            # 记一笔「这几条刚被用过」(C30): 只记账, 不进排序 —— 失败照抛,
            # 不吞 (与 list 用同一个库, 同一刻它坏了的概率微乎其微, 假装成功
            # 反而违反「坏了不能假装没坏」)
            await repository.touch_used(
                [memory.memory_id for memory in rows],
                tenant_id=tenant_id,
                user_id=user_id,
            )
        if not rows:
            return "(还没有记住这位用户的任何事: 长期记忆是空的)"
        lines = [
            f"{index}. [{KIND_BEHAVIORS[MemoryKind(memory.kind)].label}] "
            f"{memory.created_at.date().isoformat()} · {memory.content} "
            f"(编号 {memory.memory_id[:ID_DISPLAY_LENGTH]})"
            for index, memory in enumerate(rows, start=1)
        ]
        return "你记得这位用户的事 (新的在前):\n" + "\n".join(lines)

    @tool
    async def forget(
        memory_id: Annotated[
            str,
            Field(
                description="要忘掉的那条记忆的编号 (recall 结果里「编号」两个字"
                "后面的那串), 照抄即可",
                min_length=ID_DISPLAY_LENGTH,
                max_length=32,
                pattern=r"^[0-9a-fA-F]{8,32}$",
                examples=["3f2a1b7c"],
            ),
        ],
    ) -> str:
        """忘掉**当前用户**的一条长期记忆 —— 用户说「忘掉…」「别记着…」「不要
        再用那个风格了」时使用. **先从 `recall` 的编号里挑一条**, 不要凭印象编
        编号; 只删他本人名下的, 一次一条.
        """
        matches = await repository.list_by_id_prefix(
            memory_id.lower(), tenant_id=tenant_id, user_id=user_id
        )
        if not matches:
            return (
                f"没有找到编号 {memory_id} 这条记忆 —— 先用 recall 看一下当前有哪些, "
                f"照抄「编号」后面的那串再试"
            )
        if len(matches) > 1:
            return (
                f"编号 {memory_id} 对上了不止一条记忆 (少见), 请提供更长的编号 —— "
                f"照 recall 结果多抄几位即可"
            )
        await repository.soft_delete(
            matches[0].memory_id, tenant_id=tenant_id, user_id=user_id
        )
        return f"已忘掉: 「{matches[0].content}」"

    return [remember, recall, forget]
