"""身份说明的引用: 「这段会话用的是哪份提示词」记成一个可校验的引用 (帧 v5).

一句话理解: 快照要长期留在存储里, 而身份说明是**配置**而不是对话内容 —— 同一段
会话的每一帧都把那几千字正文抄一遍, 抄的还是同一份东西. 于是 v5 起帧里不再存
正文, 只存一个**引用**: 名字 + 正文的 sha256. 正文的唯一定义仍在盘上
(`{prompt_dir}/{name}.prompt`), 一版一个文件的布局保证旧版本不会消失.

为什么记 `name` 而不是裸版本号: 框架自己那份 `system.prompt` 没有版本号, 而业务的
是 `system/v2` —— 名字是 `load_prompt` 唯一的取数依据, 两种情况都覆盖得住.

为什么还要 sha: 文件被**就地改过**(没加新版本)时, 名字会撒谎, sha 不会. sha 算的是
**渲染后**的正文 (`${model_name}` 替换之后) —— 换个模型名就换一份正文, 而正文变了
前缀缓存也变了, 那是同一件事的两个后果.

四件事分开看 (别混):
- `prompt_ref` 出门: 会话装配时算一次, 之后一个字不改地进每一帧 (纯函数)
- `detach_identity` 摘正文: 落帧那一刻按引用把第 0 条的正文摘掉 (写侧, 由 loop 调)
- `deref_prompt` 进门: 按引用把正文取回来, **校验 sha**
- `restore_identity` 把正文补回一份已剥离的历史 (读侧, 由会话层调)

sha 对不上时的动作是**用当前文件 + 一条 warning**, 三条路里选中间那条: 报错会让
一次误编辑变成「所有旧会话打不开」, 静默换正文又会让「当时它看到什么」悄悄失真.
而误编辑在本仓的布局下本来就不该发生 (换一版 = 加一个文件 + 改 manifest.yaml),
所以它一出现基本等于有人违反了提示词纪律 —— 值得一条日志.

正文**取不到**(文件被删)时**不猜也不退**: 向上抛 `PromptNotFoundError`. 这与
`load.resolve_prompt_version` 那条「读不到就报错, 不静默退回上一版」是同一条纪律
—— 编不出正文还不吭声, 等于让这段会话带着别人的身份继续说话.

本模块**只被会话层调用** (client/session.py): 它是读盘动作, 而核心 agent 层不碰
磁盘 (见 `prompt/__init__.py` 那条边界) —— loop 只拿着引用这个**数据**, 自己不解释
它, 更不按它去读盘.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from CharAgent.model.utils.types import ModelMessage
from CharAgent.prompt.errors import PromptRefMismatchError
from CharAgent.prompt.load import load_prompt

# 同一棵日志树 (与 checkpoint / db 那两处同一个做法)
logger = logging.getLogger("charagent.prompt")

# 身份说明在历史里的位置与角色: 它恒为 messages[0], role 恒为 system.
#
# 为什么写死在第 0 条: 这不是本模块发明的约定, 而是既有事实 —— 会话装配时就把
# 它放在那儿 (client/session.py), 压缩策略也明写「第 0 条 (system) 永不裁」
# (agent/compaction.py). 引用机制只是把「那一条存不存正文」拿出来单独处理.
IDENTITY_ROLE = "system"

# 引用字典的两个键 (进快照的稳定契约: 改名等于让老帧读不出东西).
#
# 定义在这里而不是 checkpoint 包里: 「引用里装什么」是提示词这件事的一部分, 而
# checkpoint 只是它的一个**消费者** (写帧时按它剥离, 读帧时按它校验). 依赖方向
# 因此是 checkpoint -> prompt 单向, 两边不会成环.
REF_NAME_KEY = "name"
REF_SHA_KEY = "sha256"

# 引用在代码里的类型 (快照里它就是一个普通字符串字典: checkpoint 层不必认识
# 「提示词」这个概念, 只当它是两个字符串)
PromptRef = dict[str, str]


def sha256_text(text: str) -> str:
    """一段文本的 sha256 (十六进制小写).

    编码固定 utf-8: 写入侧与校验侧必须是同一套字节, 否则同一段正文会算出两个值
    (提示词里大量中文, 换编码就会变字节).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_ref(prompt_name: str, text: str) -> PromptRef:
    """造一个引用: `{"name": ..., "sha256": ...}` (会话装配时调一次).

    Args:
        prompt_name: 传给 `load_prompt` 的那个名字 (如 "system" / "system/v2").
        text: **渲染后**的正文 (与真正发给模型的那份逐字一致).

    Returns:
        PromptRef: 可存进快照的引用 (纯字符串字典, JSON 直接认识).
    """
    return {REF_NAME_KEY: prompt_name, REF_SHA_KEY: sha256_text(text)}


def identity_message(text: str) -> ModelMessage:
    """正文 -> 历史第 0 条那条 wire 消息.

    只有这一处拼它的形状 (会话装配、还原两处都从这里取), 免得某处少写一个 role
    或者多带一个字段 —— 那会让「同一份身份说明」在历史里长得不一样.
    """
    return {"role": IDENTITY_ROLE, "content": text}


def ref_name(ref: PromptRef | None) -> str | None:
    """引用里的提示词名; None 表示没有引用 (身份说明内联 / 根本没配).

    给「把身份说明写进运行记录」那一处用 (db/recorder.py 填 runs.prompt_version) ——
    它只要名字, 不该知道引用字典的键长什么样, 于是键名这件事仍只有本模块知道.
    """
    return None if ref is None else ref[REF_NAME_KEY]


def deref_prompt(
    ref: PromptRef,
    *,
    prompt_dir: str | os.PathLike[str] | None = None,
    model_name: str | None = None,
    context: str = "",
) -> str:
    """按引用取回身份说明正文 (并校验 sha).

    走的就是 `load_prompt` 本身 —— 渲染规则只有那一份实现, 这条路上不可能渲出与
    当初不同的文本 (除非文件或模型名真的变了, 那正是 sha 要发现的事).

    Args:
        ref: 引用 (来自帧的 `CheckpointState.prompt_ref`).
        prompt_dir: 提示词目录; None 表示框架自己的 `templates/`.
        model_name: 填 `${model_name}` 用的模型名; None 表示听 `.env` 的.
        context: 出问题时附在日志里的上下文 (如 "thread=abc"), 便于定位是哪段会话
            碰上的.

    Returns:
        str: 正文. sha 对不上时返回的是**当前文件**的正文, 同时记一条 warning.

    Raises:
        PromptNotFoundError: 这个版本的文件不在盘上了. 不猜也不退回别的版本 ——
            编不出正文还不吭声, 等于让这段会话带着别人的身份继续说话.
    """
    text = load_prompt(ref[REF_NAME_KEY], prompt_dir=prompt_dir, model_name=model_name)
    actual = sha256_text(text)
    expected = ref[REF_SHA_KEY]
    if actual != expected:
        logger.warning(
            "身份说明与帧里记的对不上 (提示词 %s%s): 帧里记的是 %s, 盘上这份是 %s. "
            "按盘上这份继续, 「当时它看到什么」在这段会话上不再精确 —— 检查这份"
            "提示词是不是被就地改过 (换版本的正确做法是加一个文件)",
            ref[REF_NAME_KEY],
            f", {context}" if context else "",
            expected[:12],
            actual[:12],
        )
    return text


def detach_identity(
    messages: list[ModelMessage], ref: PromptRef | None
) -> list[ModelMessage]:
    """落帧用: 有引用时把第 0 条那条身份说明的**正文**摘掉 (只留引用).

    为什么在**写帧那一侧**做, 而不是放在序列化里: 三个后端里只有两个会编码 (内存
    版存的是深拷贝的原对象), 放进编码器会让「帧里存不存正文」随后端而变 —— 而形状
    是「一帧是什么」的一部分, 不该取决于它躺在哪儿 (`checkpoint/memory.py` 的
    「换存储不换行为」). 于是这条规矩由**造帧的人**执行, 三个后端一视同仁.

    三种输入各走各的 (顺序即判定顺序):
    - `ref` 为 None: 原样返回 —— 正文内联在 messages[0] 里, 那一帧完全自描述
      (v4 及更早的帧, 以及没配身份说明的会话都走这条).
    - 第 0 条不是身份说明: 原样返回 —— 这段历史**已经摘过了** (典型场景: 从存储里
      读出来的帧又要写回另一个后端). 没有正文可校验, 而引用说的是「那条不在这里
      的消息是谁」, 这一层只能采信. 这条让本函数**幂等**.
    - 第 0 条是身份说明: 校验 sha 之后摘掉它 (正文由读的人按引用补回来).

    Args:
        messages: 完整历史 (账本). 调用方给的是副本, 本函数不改它.
        ref: 这一段的身份说明引用; None 表示没剥离过 (或不用剥).

    Returns:
        list[ModelMessage]: 要落帧的那份消息; 不需要摘时**返回同一个列表**.

    Raises:
        PromptRefMismatchError: 引用与第 0 条的正文对不上 (装配 bug, 见该异常).
    """
    if ref is None:
        return messages
    head = messages[0] if messages else None
    if not isinstance(head, dict) or head.get("role") != IDENTITY_ROLE:
        return messages
    content = head.get("content")
    actual = sha256_text(content) if isinstance(content, str) else ""
    if actual != ref[REF_SHA_KEY]:
        raise PromptRefMismatchError(ref[REF_NAME_KEY], ref[REF_SHA_KEY], actual)
    return messages[1:]


def detach_view_identity(
    view: dict[str, Any] | None, ref: PromptRef | None
) -> dict[str, Any] | None:
    """落帧用: 把「这一轮真发出去的那份视图」里的身份说明也换成引用 (与进度同构).

    为什么要: 视图每轮都带着第 0 条身份说明 (那几千字), 而它**就是** progress 那边
    那一条 (压缩策略拿 `history[0]` 当视图的第 0 条) —— 帧里已经有 `state.prompt_ref`
    描述它, 视图再抄一遍纯属重复.

    两条取值规则:
    - `view` 为 None (这一轮没投影过): 原样返回 None —— 没有「那一份」可描述.
    - `view["messages"]` 为 None (视图 == 账本): 不摘 (没得摘), 但**引用照记** ——
      它说的还是「这一段的身份说明在哪」, 读的人据此知道账本那条要不要补.

    Note:
        摘完的 `view["messages"]` **不再是「可以直接发给模型的完整列表」** —— 读的
        人要先按 `prompt_ref` 把正文取回来前插 (与 `restore_identity` 同一条规矩).

    Args:
        view: `agent/compaction.py` 的 `view_payload` 造出来的那份; None 表示没有.
        ref: 这一段的身份说明引用; None 表示没剥离过 (或不用剥).

    Returns:
        dict | None: 新的字典 (带 `prompt_ref` 键); `view` 为 None 时是 None.
    """
    if view is None:
        return None
    messages = view.get("messages")
    if not isinstance(messages, list):
        return {**view, "prompt_ref": ref}
    return {**view, "messages": detach_identity(list(messages), ref), "prompt_ref": ref}


def restore_identity(
    messages: list[ModelMessage],
    ref: PromptRef | None,
    *,
    prompt_dir: str | os.PathLike[str] | None = None,
    model_name: str | None = None,
    context: str = "",
) -> tuple[list[ModelMessage], PromptRef | None]:
    """把身份说明补回一份**已剥离**的历史 (会话层从帧里读回历史时调).

    **返回值是一对, 必须一起用** (`(messages, ref)`), 这条契约是本函数的核心:
    返回的 `ref` 恒与返回的 `messages[0]` **相符**. 为什么不能只还历史:

    帧里那个引用是写帧那一刻算的, 而正文可能已经不是它了 (提示词被就地改过, 或
    换了默认版本). 照旧把老引用留下来, 下一次落盘时 sha 校验会发现「引用说的和
    正文算出来的不是同一份」而**报错** —— 一次版本切换就变成这段会话再也存不下
    快照. 把实际用的那份正文重新算成引用返回, 这个坑就不存在: 新落的帧记的是
    **这段历史真正用的**那份身份说明 (与 `deref_prompt` 那条 warning 各说各的
    事实: 那条说「和帧里记的不同」, 这里给出的则是「那么现在用的是哪一份」).

    三个分支 (顺序即判定顺序):
    - `ref` 为 None: v4 及更早的帧 —— 正文本来就在 messages[0] 里, **原样返回**,
      引用也仍是 None (表示「这一段是内联的」; 于是续聊落下的新帧照样内联, 不会
      凭空给一段内联历史安上一个引用).
    - `messages[0]` 已经是身份说明: 已经补过, 原样返回, 只把引用重算成与它相符的
      那一份. **这条让本函数幂等** —— 「水合」与「收回进度」两条路可能先后都走到
      这里, 补两遍会把一份身份说明变成两份.
    - 其余: 按 `ref` 取正文前插一条, 引用按取回来的正文重算.

    Args:
        messages: 从帧里读回来的历史 (调用方给的是副本, 本函数不改它).
        ref: 帧里那个引用; None 表示这帧没剥离过.
        prompt_dir / model_name / context: 见 `deref_prompt`.

    Returns:
        tuple: (可交给 loop 的历史, 与它相符的引用 —— 直接拿去当 `prompt_ref` 用).
        不需要动时**返回同一个列表** (不复制: 没改它就没什么要保护的).

    Raises:
        PromptNotFoundError: 见 `deref_prompt` —— 这一段会话的身份说明找不回来了.
    """
    if ref is None:
        return messages, None
    if messages and messages[0].get("role") == IDENTITY_ROLE:
        content = messages[0].get("content")
        if not isinstance(content, str):
            return messages, ref
        return messages, prompt_ref(ref[REF_NAME_KEY], content)
    text = deref_prompt(
        ref, prompt_dir=prompt_dir, model_name=model_name, context=context
    )
    return [identity_message(text), *messages], prompt_ref(ref[REF_NAME_KEY], text)


__all__ = [
    "IDENTITY_ROLE",
    "REF_NAME_KEY",
    "REF_SHA_KEY",
    "PromptRef",
    "deref_prompt",
    "detach_identity",
    "detach_view_identity",
    "identity_message",
    "prompt_ref",
    "ref_name",
    "restore_identity",
    "sha256_text",
]
