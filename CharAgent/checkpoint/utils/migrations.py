"""快照格式的版本迁移: 让老存档读得回来 (difficulties #5 向前兼容).

一句话理解: 存档存在硬盘上, 而代码会升级. 今天写出来的存档, 三个月后字段可能
改了 —— 那时候读三个月前的老存档, 不能直接报错说「看不懂」. 办法是给每份存档
盖一个版本号 (schema_version), 读到老版本时先用**翻译函数**把它升级成现在的
样子, 再交给后面的代码用.

读到一份存档时按三种情况处理:
1. 版本比当前旧 -> 依次调用翻译函数, 1→2→3... 一路升到当前版本
2. 版本等于当前 -> 不用动
3. 版本比当前新 -> **报错**, 不硬读. 那是来自未来的格式 (用更新的代码写下的),
   硬读会把字段读错位, 悄悄给出一份错的进度, 比报错危险得多. 唯一正确的做法
   是升级代码.

**翻译函数的输入是「记录主体」(body)**: `{"state": 进度, "metadata": 观察值}`
—— 因为真实的迁移常常要**在两者之间搬字段** (v2→v3 就是这么搬的), 只给 state
的翻译函数根本做不到. 两个存储都先把主体拼出来再交给这里 (Redis 的整条记录、
Postgres 的 state 列 + metadata 列).

版本历史 (只加不改, 老格式永远能读):

- **v1 (早期原型)**: 只有消息列表 —— state 就是裸列表, 那时候「说过什么话」就
  够接着跑了; 计数器、观察值、挂起信息都还没出现.
- **v2**: state 变成结构化字典 (messages + 计数器 + 正文片段), 并带上 content /
  finish_reason / outcome 三个「当时答成什么样」的字段 —— 它们当时和进度混在
  一起, 回头看是没分清「恢复要用的」与「给人看的」.
- **v3**: 把上面那三个观察值搬进 metadata, 并补上 source / turn_tokens /
  turn_elapsed_ms / tool_names 四个调试字段 (「哪一步最贵、这一步是怎么来的」).
  于是 state 只剩「接着跑必需的输入」, metadata 专管「这一步发生了什么」.
- **v4**: 进度多两样上下文压缩 (#7) 的账 —— summary (摘要正文) 与
  summary_covers (它压到第几条). 老帧没有摘要, 补 None / 0, 那正是当时的事实.
- **v5 (当前)**: 进度多两样东西 —— ① 身份说明的**引用** (prompt_ref): 老帧的
  正文本来就在 messages[0] 里, 于是补 None = 「不用补」; ② 五个**归因**分量
  (input / output / reasoning / cache_hit / cache_miss), 老帧补 None = 「那时
  上游还没上报过这些」. 两样都不动老帧的 messages.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from CharAgent.checkpoint.utils.errors import CheckpointMigrationError
from CharAgent.checkpoint.utils.types import SCHEMA_VERSION

# v2 时混在 state 里的观察值字段 (v3 把它们搬进 metadata; 见模块 docstring)
_V2_OBSERVATION_KEYS = ("content", "finish_reason", "outcome")

# v3 新增的调试字段默认值 (老帧不知道这些, 填「不知道」的诚实默认: 算 0 / 空)
_V3_METADATA_DEFAULTS: dict[str, Any] = {
    "source": "loop",  # 老帧一律按「正常跑出来的一轮」记 (那时还没有分叉/挂起标记)
    "turn_tokens": 0,
    "turn_elapsed_ms": 0.0,
    "tool_names": [],
}

# 翻译函数表: 键是「从哪个版本出发」, 值是「怎么升到下一版」
# (1: _v1_to_v2 表示把 v1 的主体升成 v2 的主体)
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}

# v5 给进度补的两类字段默认值: 老帧一律「那时候还没有这两个概念」. 两类默认值
# 说的**不是同一件事**, 所以分开写清楚 (别读成一句「老帧不知道」):
#
# - prompt_ref=None: 不是「那时候没有身份说明」, 而是「**正文就内联在 messages
#   那一侧**」—— 老帧仍然完全自描述, 读回来一个字都不必补.
# - 五个归因分量 = None: 「上游一次都没上报过这个分量」. 填 0 会把「当时没这个
#   字段」说成「当时确实为零」, 而这两个在成本归因里是相反的结论.
_V5_STATE_DEFAULTS: dict[str, Any] = {
    "prompt_ref": None,
    "input_tokens": None,
    "output_tokens": None,
    "reasoning_tokens": None,
    "cache_hit_tokens": None,
    "cache_miss_tokens": None,
}


def migrate_body(body: dict[str, Any], *, from_version: int) -> dict[str, Any]:
    """把某个版本的记录主体逐级升级到当前版本.

    Args:
        body: 记录主体 `{"state": ..., "metadata": ...}` (字段可能缺失).
        from_version: 这份记录当初写下的版本号 (存储在记录或列里).

    Returns:
        dict: 升到当前版本后的主体 (内部字段仍可能缺失, 由调用方填默认值).

    Raises:
        CheckpointMigrationError: 版本比本代码新; 中间某一级没有翻译函数;
            或升完之后 state 不是字典 (结构坏了).
    """
    if from_version > SCHEMA_VERSION:
        raise CheckpointMigrationError(
            f"快照格式版本 v{from_version} 比本代码支持的 v{SCHEMA_VERSION} 新, "
            f"读不了 (来自更新的框架); 请升级代码后再读"
        )
    current = body
    version = from_version
    while version < SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise CheckpointMigrationError(
                f"缺少 v{version} -> v{version + 1} 的迁移函数, "
                f"无法把老快照读到 v{SCHEMA_VERSION}"
            )
        current = step(current)
        version += 1
    state = current.get("state")
    if not isinstance(state, dict):
        raise CheckpointMigrationError(
            f"迁移完成后 state 应当是字典, 实际为 {type(state).__name__}"
            f" (快照版本 v{from_version})"
        )
    return current


def _v1_to_v2(body: dict[str, Any]) -> dict[str, Any]:
    """v1 的裸消息列表 -> v2 的结构化 state (v1 只有消息, 别的概念当时还没有).

    Args:
        body: v1 的主体 —— 其中 `state` 是消息列表.

    Returns:
        dict: 主体, 其 state 变成 `{"messages": [...]}`; 计数器缺席, 由读取方填默认.

    Raises:
        CheckpointMigrationError: v1 的 state 不是列表 (结构坏了).
    """
    state = body.get("state")
    if not isinstance(state, list):
        raise CheckpointMigrationError(
            f"v1 的 state 应当是消息列表, 实际为 {type(state).__name__}"
        )
    upgraded = dict(body)
    upgraded["state"] = {"messages": list(state)}
    return upgraded


def _v2_to_v3(body: dict[str, Any]) -> dict[str, Any]:
    """v2 -> v3: 观察值从 state 搬进 metadata, 并补上调试字段的默认值.

    这是最常见的一类真实迁移 —— **字段搬家**: 字段名和值都没变, 只是换了个家
    (因为回头看发现它本来就该属于另一边). 搬完老快照读回来的对象与新写的对象
    结构一致, 上层代码不必为「老数据」多写一个分支.
    """
    upgraded = dict(body)
    state = dict(upgraded["state"])
    metadata = dict(upgraded.get("metadata") or {})

    for key in _V2_OBSERVATION_KEYS:
        if key in state:
            # setdefault: 万一将来出现「两边都有」的怪数据, 以 metadata 为准
            metadata.setdefault(key, state.pop(key))

    for key, default in _V3_METADATA_DEFAULTS.items():
        metadata.setdefault(key, default)

    upgraded["state"] = state
    upgraded["metadata"] = metadata
    return upgraded


# v4 给进度补的两个压缩字段默认值: 老帧一律「那时候还没压过」
_V4_STATE_DEFAULTS: dict[str, Any] = {
    "summary": None,
    "summary_covers": 0,
}


def _v3_to_v4(body: dict[str, Any]) -> dict[str, Any]:
    """v3 -> v4: 进度里补上上下文压缩的两个字段 (老帧没有摘要, 填「没有」).

    这是最常见的一类向后兼容迁移 —— **纯加字段**: 老记录缺的那两个字段用当时
    的事实填上 (那时候确实没压过), 读回来之后与新写的记录结构一致, 上层代码
    不必为「老数据」多写一个分支.
    """
    upgraded = dict(body)
    state = dict(upgraded["state"])
    for key, default in _V4_STATE_DEFAULTS.items():
        state.setdefault(key, default)
    upgraded["state"] = state
    return upgraded


def _v4_to_v5(body: dict[str, Any]) -> dict[str, Any]:
    """v4 -> v5: 进度里补上身份说明引用与五个归因分量的默认值 (见上面的常量).

    三类迁移里它又是**纯加字段**那一类 —— 但注意它**不动 messages**: v5 的剥离
    只发生在**写侧**, 而且只在调用方给了引用时才剥 (见 serialization 的
    `_body_payload`). 老帧里那条身份说明正文照旧留着, 于是「读三个月前的快照
    重放」仍然拿得到当时逐字的原文, 不依赖盘上那份提示词还在不在.
    """
    upgraded = dict(body)
    state = dict(upgraded["state"])
    for key, default in _V5_STATE_DEFAULTS.items():
        state.setdefault(key, default)
    upgraded["state"] = state
    return upgraded


MIGRATIONS[1] = _v1_to_v2
MIGRATIONS[2] = _v2_to_v3
MIGRATIONS[3] = _v3_to_v4
MIGRATIONS[4] = _v4_to_v5
