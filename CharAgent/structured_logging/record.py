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

大白话版: 一条记录里哪些键是「业务自己放的内容」, 这件事只有一处说了算.
"""

from __future__ import annotations

import logging
from typing import Any

# LogRecord 自带的属性 (name / levelno / pathname / created / msg / args ...)
STANDARD_ATTRS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


def extras_of(record: logging.LogRecord) -> dict[str, Any]:
    """这条记录里由调用方 (`extra=`) 放进来的那些键.

    Returns:
        dict[str, Any]: 结构字段的**浅**拷贝 (调用方拿去改不会动到记录本身);
        没有就返回空字典.
    """
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in STANDARD_ATTRS
    }
