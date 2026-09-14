"""历史视图: 把一串快照渲染成人一眼能看懂的表格 (回放调试用, issue 07).

一句话理解: 翻历史 (saver.list_history) 拿到的是 Checkpoint 对象列表 —— 里面
全是嵌套字段, 调试时盯着 JSON 想「第 3 步为什么这么贵? 这一帧是从哪儿冒出来的?」
很费劲. 本文件把每帧压成一行, 再对齐成表格, 一行一个问题.

三件事:
- `summarize(frame)`: 一帧 -> 关键字段的字典 (编号截短 / 轮次 / 来源 / 本轮用量
  / 工具 / 结束原因 / 是否挂起 / 父帧)
- `format_history(frames)`: 摘要列表 -> 对齐的文本表格 (直接 print 就能看)
- 分叉怎么看: 每行的「父帧」列写着上一帧的编号; 当某一帧的父帧**不是**它上一行
  时, 那一行就是从老快照分出去的新线 (time-travel 的痕迹一眼可见)

为什么放在 utils: 纯函数、无状态, 与 pending.py 同类 (从已有数据里算出人能直接
用的东西), 不掺存储与协议的事.

中文对齐的注意点: 汉字在终端里占**两格**, 按字符个数补空格会越排越歪 —— 所以
宽度用 display_width 按「终端格数」算, 而不是 len().
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unicodedata import east_asian_width

from CharAgent.checkpoint.utils.types import Checkpoint

# 表格里编号截短的位数 (够区分一屏内的帧, 又不撑爆行宽)
SHORT_ID_LEN = 8

# 摘要字段的顺序即表格列顺序 (改了这里, 表格自动跟着改)
_COLUMNS = ("帧", "轮次", "来源", "本轮tok", "本轮ms", "工具", "结束", "挂起", "父帧")

# 列与列之间的分隔 (两个空格: 比竖线干净, 复制进表格软件也不碍事)
_SEPARATOR = "  "


def display_width(text: str) -> int:
    """一段文本在终端里占几格 (汉字等全角字符算 2 格, 其余算 1 格)."""
    return sum(2 if east_asian_width(char) in "WF" else 1 for char in text)


def short_id(checkpoint_id: str) -> str:
    """编号截短 (表格里够区分即可; 完整编号在对象里随时可取)."""
    return checkpoint_id[:SHORT_ID_LEN]


def summarize(frame: Checkpoint) -> dict[str, Any]:
    """一帧 -> 一行摘要 (键与 _COLUMNS 对应, 值是给人看的短文本).

    Args:
        frame: 一帧快照.

    Returns:
        dict: 这一帧的关键字段; 「结束」优先取 run 结束原因, 其次取最后一次响应
        的终止原因, 都没有就 "-" (还在跑); 「挂起」没挂着就是 "-".
    """
    metadata = frame.metadata
    suspension = frame.state.suspension
    return {
        "帧": short_id(frame.checkpoint_id),
        "轮次": frame.turn_number,
        "来源": metadata.source.value,
        "本轮tok": metadata.turn_tokens,
        "本轮ms": round(metadata.turn_elapsed_ms, 1),
        "工具": ", ".join(metadata.tool_names) or "-",
        "结束": metadata.outcome or metadata.finish_reason or "-",
        "挂起": "-" if suspension is None else suspension.reason,
        "父帧": short_id(frame.parent_id) if frame.parent_id else "(根)",
    }


def format_history(frames: Sequence[Checkpoint]) -> str:
    """把一串快照渲染成对齐的文本表格 (按传入顺序; 通常来自 list_history).

    Args:
        frames: 快照列表 (空列表给一句提示, 而不是返回空串 —— 调试时「没有历史」
            和「打印失败」要能分清).

    Returns:
        str: 多行文本, 第一行是表头; 每行内容按终端显示宽度对齐.
    """
    if not frames:
        return "(没有历史帧: 这个会话没存过, 或者存储不支持历史)"

    rows = [summarize(frame) for frame in frames]
    widths = {
        column: max(
            display_width(column), *(display_width(str(row[column])) for row in rows)
        )
        for column in _COLUMNS
    }

    def render(row: dict[str, Any]) -> str:
        cells = []
        for column in _COLUMNS:
            text = str(row[column])
            # 数字列右对齐 (一眼比大小), 其余左对齐
            if column in {"轮次", "本轮tok", "本轮ms"}:
                cells.append(" " * (widths[column] - display_width(text)) + text)
            else:
                cells.append(text + " " * (widths[column] - display_width(text)))
        # 刻意不 rstrip: 末列的补白是表格宽度的一部分, 裁掉它各行就不等宽了
        return _SEPARATOR.join(cells)

    header = _SEPARATOR.join(
        column + " " * (widths[column] - display_width(column)) for column in _COLUMNS
    )
    rule = "-" * display_width(header)
    return "\n".join([header, rule, *(render(row) for row in rows)])
