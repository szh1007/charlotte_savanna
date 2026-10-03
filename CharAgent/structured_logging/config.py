"""框架的日志出口: 一处配置 (`configure_logging`) + 一处取号 (`get_logger`).

一句话理解: 本片之前, 六个模块各自 `logging.getLogger("charagent.x")` 打自己的,
而**没有一个地方**决定这些日志长什么样、写到哪儿 —— 那是部署方的事, 于是默认情况
下它们连 handler 都没有 (`logging` 的 lastResort 只兜 WARNING 以上, 且不带任何
格式). 现在框架交出一个入口: 业务在启动时调一次, 出口就定下来了.

| 名字 | 干什么 |
|------|--------|
| `configure_logging` | 装一个 handler 到**根**上: 打码 (filter) + 一种格式 |
| `get_logger` | 取框架自己的 logger (名字一律落在 `charagent` 这棵树下) |
| `LOGGER_ROOT` | 那棵树的名字 |

**为什么是根而不是 `charagent` 那棵树**: 一个进程只该有一个出口 —— 框架的行与业务
自己的行必须长得一样, 否则「日志是 JSON, 一事件一行」只对一半的行成立, 而按行
消费的那一头会在另一半上崩 (`jq` 遇到非 JSON 的行直接报错). 挂在根上还带来一个
副作用: `httpx` 这类库的行也会进来, 于是下面把三家吵闹的压到 WARNING.

**装配期调一次**: 它改的是全局状态 (根 logger 的 handler 与 level), 不该在每次
请求里跑 —— 与「快照后端 / 模型适配器是进程级的」同一条道理.

大白话版: `main` 里调一次 `configure_logging`, 之后全进程的日志都从这里出去;
写日志用 `get_logger("db")` 取一个, 名字会自动落在 `charagent.db` 上.
"""

from __future__ import annotations

import logging
import sys
from typing import IO, Any

from CharAgent.redact.protocol import Redactor
from CharAgent.structured_logging.filter import RedactFilter
from CharAgent.structured_logging.formatter import JsonFormatter, PlainFormatter
from CharAgent.structured_logging.utils.errors import LoggingConfigError

# 框架自己的 logger 都在这棵树下 (`charagent.agent` / `charagent.server` ...).
# 名字是**选择器**: 部署想把框架调到 DEBUG 而不动别家, 靠的就是这个前缀
LOGGER_ROOT = "charagent"

# 装上去的那个 handler 打这个记号. 再配一次时先摘掉旧的 —— 否则每行日志写两遍,
# 而「写两遍」在排查时会变成「是不是有两处逻辑都在打这一条」的假线索
_HANDLER_MARK = "_charagent_logging_handler"

# 每个请求都要打一行 INFO 的三家: 一次问答十几次工具调用, 真正有用的那几行会被
# 冲走. 这不是「少记一点」, 是「让日志读得下去」的底线 —— 真要它们, 自己调回去:
# `logging.getLogger("httpx").setLevel(logging.DEBUG)` (用例里动了它们记得还原,
# 见 `CharAgent.structured_logging.testing`)
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3")


def get_logger(name: str) -> logging.Logger:
    """取框架的 logger (名字一律落在 `charagent` 这棵树下).

    **为什么要有这个工厂** (直接用 `logging.getLogger(__name__)` 不行吗): 名字是
    上面的那个选择器, 而 `__name__` 在包内部是 `CharAgent.db.recorder` (大小写与
    层级都对不上) —— 拿它建的 logger 不在那棵树下, 「只调框架那几层」的那一句
    `setLevel` 于是一行也调不到, 且**不报错**. 工厂把这条命名规矩钉在一处.

    Args:
        name: 短名 (`"db"` / `"server"`) 或完整的树内名 (`"charagent.db"`).

    Returns:
        logging.Logger: 那一个 logger (同一个名字恒返回同一个对象).

    Raises:
        LoggingConfigError: 名字是空的 (空名会建出根 logger, 那是个别的东西).
    """
    if not name or not name.strip():
        raise LoggingConfigError("logger 名不能是空的")
    short = name.strip()
    if short == LOGGER_ROOT or short.startswith(f"{LOGGER_ROOT}."):
        return logging.getLogger(short)
    return logging.getLogger(f"{LOGGER_ROOT}.{short}")


def configure_logging(
    *,
    level: int | str,
    redactor: Redactor,
    json: bool = True,
    stream: IO[str] | None = None,
) -> None:
    """把这个进程的日志出口定下来 (装配期调一次).

    幂等: 再调一次会先把上一轮装的那个摘掉 (`_HANDLER_MARK` 认得出它).

    Args:
        level: 根 logger 的级别 (数字或名字, 如 `logging.INFO` / `"INFO"`).
        redactor: 打码员 (`redact` 包的 `Redactor` 协议). **必填** —— 不给就是
            「这个进程的日志不打码」, 而那种默认值迟早会在某个不该开的场合生效
            (ADR-0019 已否过一次「演示时关掉脱敏」的开关). 只用框架、没有自己的
            字段名单的部署传一个空的 `RuleRedactor()`: 自由文本那四条通用规则
            照常生效.
        json: True (默认) 一事件一行 JSON; False 走给人念的一行 (字段一个不少,
            见 `PlainFormatter`).
        stream: 写到哪儿; None = `sys.stderr` (与 `logging` 自己的默认一致).

    Raises:
        LoggingConfigError: `level` 是个认不出的字符串.

    Note:
        不给返回值 (与 `logging.basicConfig` 一致): 它装的是**进程的**出口, 调用方
        没有理由攥着那个 handler —— 真要调它 (改级别 / 换流), 走 `logging` 自己那套
        (`logging.getLogger().handlers`). 用例要接日志用
        `CharAgent.structured_logging.testing.logging_to` (装完还负责还原).
    """
    handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(JsonFormatter() if json else PlainFormatter())
    # 工序挂在 handler 上而不是 logger 上: logger 上的 filter 只管**直接**打在它
    # 身上的那条 (向上传播时不再过祖先的 filter), 而 handler 上的这道是全部记录
    # 都要过的 —— 本层要的正是「一个出口, 全都过」
    handler.addFilter(RedactFilter(redactor))
    setattr(handler, _HANDLER_MARK, True)

    root = logging.getLogger()
    for installed in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(installed)
        installed.close()
    root.addHandler(handler)
    root.setLevel(_level_of(level))
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _level_of(level: int | str) -> int:
    """级别名 / 数字 → 数字.

    Raises:
        LoggingConfigError: 认不出的名字 —— 它的表现是「那一档日志全不见了」,
            而没有任何别的症状 (与脱敏声明写错同一类: 静默失效).
    """
    if isinstance(level, int):
        return level
    resolved: Any = logging.getLevelName(str(level).strip().upper())
    if not isinstance(resolved, int):
        raise LoggingConfigError(
            f"认不出的日志级别: {level!r}"
            f" (写 INFO / WARNING 这样的名字, 或 20 这样的数字)"
        )
    return resolved
