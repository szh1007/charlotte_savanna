"""出口上的一道工序: 写出去之前先打码 (difficulties #38, 接 #26).

一句话理解: 日志 handler 上挂一道 filter, 每条记录在**格式化之前**先过一遍打码员
—— 于是真正写出去的那份里没有原文, 而格式化看到的已经是打过码的字.

**顺序是关键, 而且它不是靠人记得**: `logging.Handler.handle` 先跑 filter 再跑
`emit` (格式化发生在 emit 里), 我们只是把工序挂在那一步上. 反过来 (先格式化再
打码) 的话, 打码员面对的是一整行拼好的文本, 认不出哪个格子是什么.

三个落点都要过, 少一个就漏一条路:

| 落点 | 是什么 | 谁在用 |
|------|--------|--------|
| `record.msg` + `record.args` | 拼好的一句话 | 全部既有日志 |
| `record.__dict__` 里的结构字段 | `extra=` 传进来的那份 dict | 结构化日志 |
| `exc_info` → `record.exc_text` | 异常栈的整段文本 | 框架自己打的 traceback |

**第三个是本片的核心** (`DESIGN.md` #38 点名的那一条): issue 29 真机验过 —— 同一段
供应商正文, 重试提示那一行过了业务的出口是 `138****0003`, 而框架
`charagent.client` 打的「运行异常终止 + traceback」里还是原文. 原因是那条日志
不经过业务递给框架的 `writer`. 现在它在**同一个出口**上, 于是过同一道工序
(见 `CharApp/docs/adr/0019` 的「代价与边界」第一条).

**为什么 traceback 要先格式化再打码**: `logging.Formatter` 见到 `record.exc_text`
已经在了就不再自己格式化 —— 于是把「格式化 → 打码 → 写回」这条链钉在 filter 里,
后面接什么 formatter 拿到的都是打过码的那份 (`logging.Formatter` 与 `JsonFormatter`
都吃这一条).

大白话版: 日志出门口站着一个打码员, 每条记录出门前它都要翻一遍 —— 正文、结构化
字段、异常栈, 一个都不放过.
"""

from __future__ import annotations

import logging
import traceback

from CharAgent.redact.protocol import Redactor
from CharAgent.structured_logging.record import extras_of


class RedactFilter(logging.Filter):
    """把一条记录里的敏感值换成打码后的样子 (原地改, 恒返回 True).

    **原地改而不是另造一条**: 格式化那一端拿到的必须只有这一份 —— 两份会让「打过码
    的那份」与「真正写出去的那份」分家, 而本层的全部意义就是它们是同一份 (与
    `redaction.py` 原地改事件对象同一条推理).

    **恒返回 True**: 这是一道工序, 不是一道闸门 (拦不拦一条日志是另一回事, 由
    级别决定).

    Args:
        redactor: 打码员 (框架 `Redactor` 协议: `redact_text` + `redact_fields`).
            必填 —— 不给就是「这个出口不打码」, 而那种默认值迟早会在某个不该开的
            场合生效 (ADR-0019 已否过一次「演示时关掉脱敏」的开关).
    """

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        """打码三处, 恒放行."""
        self._redact_message(record)
        self._redact_fields(record)
        self._redact_exception(record)
        return True

    def _redact_message(self, record: logging.LogRecord) -> None:
        """拼好那句话 (`msg % args`) 打码之后写回 `msg`, 并把 `args` 清空.

        为什么先拼再打: 参数是按 `%s` / `%r` 分开给的 (`logger.warning("失败: %r",
        exc)`), 而敏感值可能只出现在其中一个参数里 —— 分开打要猜哪个参数是什么,
        拼起来打就是一句话, 按形状认正是 `redact_text` 的本职.

        为什么必须清空 `args`: 留着的话格式化时会把打过码的 `msg` 再按 `%` 插一遍
        —— 而 `msg` 里那些 `%r` 已经没有参数可对应, 于是抛 `TypeError`.

        `msg` 不是字符串的记录 (极少: `logger.info({"a": 1})`) 也走这条路 —— 它被
        插值成一个字符串, 与 stdlib 自己的做法一致 (结构化那条路是 `extra=`, 见
        `_redact_fields`).
        """
        record.msg = self._redactor.redact_text(record.getMessage())
        record.args = ()

    def _redact_fields(self, record: logging.LogRecord) -> None:
        """`extra=` 传进来的那些格子按**声明**打 (这是主手段, 见 redact 包).

        名单是业务的知识 (哪个字段是手机号), 规矩是框架的 (手机号长什么样) ——
        两边在这里第一次真正接上: 此前 `redact_fields` 那一半没有生产调用方
        (日志全是自由文本), 结构化日志落地的这一刻它才有东西可打.
        """
        extras = extras_of(record)
        if not extras:
            return
        record.__dict__.update(self._redactor.redact_fields(extras))

    def _redact_exception(self, record: logging.LogRecord) -> None:
        """异常栈: **先格式化再打码** (本片的核心那一条).

        `record.exc_info` 是 `(类型, 值, traceback)` 三元组, 而落进日志的是一段多行
        文本 —— 那段文本里既有异常自己的话 (`str(exc)`, 供应商回的错误正文常常整个
        塞在这里), 也有每一帧的源码行. 于是只能整段按形状打.

        只在 `exc_text` **还是空**的时候自己格式化 (它非空 = 已经有人算过了: 可能
        是上一个 handler 的 formatter, 那它已经过过这道工序; 也可能是我们这一轮
        `redact_text` 的结果 —— 再算一遍会把打过码的盖回原文).

        `stack_info` (调用方 `stack_info=True` 要的那段调用栈) 一并过一遍: 它与
        traceback 是同一类东西 (一段代码路径文本), 只是今天没人用它 —— 留一条路
        不遮, 迟早要在某次排查里被发现.
        """
        if record.exc_info and not record.exc_text:
            record.exc_text = "".join(traceback.format_exception(*record.exc_info))
        if record.exc_text:
            record.exc_text = self._redactor.redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redactor.redact_text(record.stack_info)
