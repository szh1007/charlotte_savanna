"""structured_logging 包: 结构化日志 + 三个 id 贯穿 (difficulties #38).

一句话理解: 日志从「一句给眼睛看的话」变成「一行一个 JSON」, 并且每一行都带着
**它属于哪次运行**的号牌 —— 出问题时能顺着号牌把一次运行的所有痕迹捞出来.

| 模块 | 一句话 |
|------|--------|
| `config.py` | 出口: `configure_logging` (装 handler) + `get_logger` (取号) |
| `context.py` | 三个 id 的携带 (`contextvars`) + `TraceIds` / `log_context` |
| `record.py` | 一条记录里「哪些键是业务放的内容」的判定 |
| `filter.py` | 出口上的一道工序: 写出去**之前**先打码 (接 #26) |
| `formatter.py` | 一事件一行 (JSON / 给人念的纯文本) |
| `utils/` | 静态支撑: `LoggingError` / `LoggingConfigError` |

**它闭合的那条链**: 脱敏 (#26, 已落) → **结构化日志 (#38, 本包)** → trace 回放
(#41, `client/trace.py` 已落). 三件事连起来才是完整的那个问题 —— 「出问题能不能
查到当时它看到了什么」: 日志告诉你**哪一次运行**出了问题 (三个 id), 而那个 `run_id`
拿去 `python -m CharAgent.client.trace <run_id>` 就能看见**当时它看到了什么**
(每轮的视图 / 工具调用 / 账).

**脱敏与格式化的顺序在这里是结构性的, 不是纪律**: 打码是 handler 上的 filter,
格式化发生在它之后的 `emit` 里 —— 顺序由 `logging` 自己的调用链决定, 不靠谁记得.

**零新依赖**: JSON 是自己拼的 (一个 `json.dumps`), 不引 structlog / loguru ——
「零框架依赖」是这个项目立身的那句话, `pyproject.toml` 里那八项就是它的证据,
不能为了日志破例.

**不做的事**: 不接日志平台 (ELK / Loki) —— 那是部署的事, 而本包交出去的是一行行
标准 JSON, 任何采集器都吃得下; 不做日志采样 / 异步落盘 (量级远没到那个份上).

**为什么叫 `structured_logging`, 不叫 `logging`** (改名是刻意的, 不是随手选的长名字):
本包一开始就叫 `logging`, 而那个名字**会遮蔽标准库** —— 谁把 `CharAgent/` 放进
`sys.path` 的**前面**, 谁的 `import logging` 就撞上本包, 症状是
`ModuleNotFoundError: No module named 'CharAgent'` 从一行 `import logging` 里抛出来,
看着莫名其妙. 实测的触发方式只有一个 (**cwd 恰好是 `CharAgent/`**, 因为
`python -m pytest` 会把 cwd 放在 `sys.path[0]`), 但「一个能让人撞上标准库的名字」
本身不该留在一个要给人跑的作品里 —— 而改名的代价只是一次 `git mv` 加十来处 import.
现在这个名字与任何顶层模块都不会撞, 而且它说的正是这件事: **结构化**日志.

大白话版: 先 `configure_logging(level=..., redactor=...)` 一次, 再用
`get_logger("db")` 取 logger; 每行日志都是 JSON, 都带 `thread_id` / `run_id` /
`request_id` 三个字段 (没有的那个是 `null`), 敏感值在写出去之前就打了码.
"""

from __future__ import annotations

from CharAgent.structured_logging.config import (
    LOGGER_ROOT,
    configure_logging,
    get_logger,
)
from CharAgent.structured_logging.context import (
    ID_FIELDS,
    TraceBinding,
    TraceIds,
    current_ids,
    log_context,
)
from CharAgent.structured_logging.filter import RedactFilter
from CharAgent.structured_logging.formatter import JsonFormatter, PlainFormatter
from CharAgent.structured_logging.utils.errors import LoggingConfigError, LoggingError

__all__ = [
    "ID_FIELDS",
    "LOGGER_ROOT",
    "JsonFormatter",
    "LoggingConfigError",
    "LoggingError",
    "PlainFormatter",
    "RedactFilter",
    "TraceBinding",
    "TraceIds",
    "configure_logging",
    "current_ids",
    "get_logger",
    "log_context",
]
