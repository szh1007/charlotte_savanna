"""structured_logging 包异常语义 (difficulties #38).

对齐 retry/utils/errors.py 与 redact/utils/errors.py 的错误族组织 (纯 Exception
基类):
- LoggingError: 本包错误基类.
- LoggingConfigError: 装配写坏了 (级别名认不出 / `log_context` 里 id 名写错 /
  logger 名是空的).

**为什么配置错要当场报**: 日志这一层的错误几乎全是**静默**的 —— 级别名打错一个
字母, 表现不是报错而是「那一档日志全不见了」; `log_context(thread=...)` 少写一个
`_id`, 表现是「那个号没绑上」, 而日志照写, 于是三个字段里少一个而不报错. 与其
等哪天在日志里发现, 不如在装配那一刻说清 (与 `RedactConfigError` 同一条纪律).

大白话版:
- 这是「日志配置写错了」这种错误的定义.
- 认不出的级别名 / 写错的 id 名, 都在当场拦下 —— 晚一步发现, 那些日志已经写出去
  了, 而错的那一份没有任何症状.
"""

from __future__ import annotations


class LoggingError(Exception):
    """structured_logging 包错误基类.

    (纯 Exception: 本包错误均为接线 / 配置期的编程错误.)
    """


class LoggingConfigError(LoggingError):
    """配置写坏了 (级别名认不出 / id 名写错 / logger 名是空的).

    报错信息里带上**认识的那几个名字**, 让写错的人当场能改对 (与 `RedactConfigError`
    列出规则名同一个用意).
    """
