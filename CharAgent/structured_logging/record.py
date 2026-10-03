"""一条日志记录里「哪些键是内容」的判定 (filter 与 formatter 共用).

一句话理解: `LogRecord` 的 `__dict__` 里混着两类键 —— 一类是 logging 自己填的
(`name` / `levelno` / `pathname` / `created` ...), 另一类是调用方用 `extra=` 放进
来的**结构化字段**. 要把后者原样写进 JSON, 就得先把两者分开.

**为什么这张表是算出来的而不是抄下来的**: `LogRecord` 自带哪些属性随 Python 版本
变 (3.12 多了 `taskName`, 更早的没有), 手抄的那份会漂. 而漂掉的表现很隐蔽 ——
某个自带属性被当成业务字段送进打码员 (运气好只是白跑一趟), 或者反过来, 真字段
漏出名单之外. 于是这里造一条空记录, 直接取它的 `__dict__`.

**名单之外的两个例外** (`message` / `asctime`): 它们不在 `__dict__` 里, 而是
`Formatter` 在格式化那一刻**塞进去的** (自定义 formatter 会往里写). 同一条记录
被两个 handler 处理时, 先跑的那个留下的 `message` 会在第二个 handler 眼里长得像
业务字段 —— 于是把它们一并排除 (只排不取, 免得某个 formatter 的产物被当成业务
数据打码).

**排掉的两种**: 一类是 logging 自己填的 (上面那张算出来的表), 一类是少数第三方库
塞进来的**噪音键** (`IGNORED_EXTRAS`, 现在只有 uvicorn 的 `color_message` —— 同一句
话给它自己那行彩色输出用的副本). 两者都不该进结构化字段: 前者是骨架, 后者是重复.

大白话版: 一条记录里哪些键是「业务自己放的内容」, 这件事只有一处说了算.
"""

from __future__ import annotations

import logging
from typing import Any

# LogRecord 自带的属性 (name / levelno / pathname / created / msg / args ...)
STANDARD_ATTRS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}

# **噪音键**: 第三方库塞进 `extra=`、对我们没有任何信息的那几个键. 进这张表要满足
# 两条: ① 它不是那条消息本身 (只是同一句话给另一种渲染方式用的副本); ② 我们没有任何
# 地方读它. 两条都满足才排 —— 宁可多看一个字段, 也不要凭「这个键看着没用」把真信息
# 丢掉 (排掉的东西不出现在任何一处日志里, 排查时才发现就晚了).
#
# 现在只有 uvicorn 的 `color_message`: 它给自己那行**彩色**输出用的模板串, 正文与我们
# 写出去的那句 `msg` 逐字同义 (uvicorn 的默认 formatter 有颜色时才读它) —— 于是它在
# 我们的 JSON 里只是一份重复 (2026-10-05 用户报的: 启动 charapp 时满屏这个字段).
IGNORED_EXTRAS: frozenset[str] = frozenset({"color_message"})


def extras_of(record: logging.LogRecord) -> dict[str, Any]:
    """这条记录里由调用方 (`extra=`) 放进来的那些键 (噪音键除外, 见上).

    Returns:
        dict[str, Any]: 结构字段的**浅**拷贝 (调用方拿去改不会动到记录本身);
        没有就返回空字典.
    """
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in STANDARD_ATTRS and key not in IGNORED_EXTRAS
    }
