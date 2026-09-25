"""redact 包异常语义 (difficulties #26).

对齐 retry/utils/errors.py 与 hooks/utils/errors.py 的错误族组织 (纯 Exception
基类):
- RedactError: 本包错误基类.
- RedactConfigError: 字段声明写坏了 (规则名不认识 / 路径不是字符串 / 路径为空).

**为什么配置错要当场报**: 脱敏失效的表现不是报错, 而是**静默地漏** —— 声明里少写
一个字段名, 日志照写、程序照跑, 值就那样出去了, 而且没有任何症状. 所以它与
`PricingNotReadyError` 同一条纪律: 在**装配期** (构造 `RuleRedactor` 那一刻) 说清,
而不是等日志真要写的时候才发现打码没生效.

大白话版:
- 这是「脱敏的声明写错了」这种错误的定义.
- 认不出的规则名 / 空路径, 都在造对象的时候直接拦下 —— 那种错误晚一步发现就等于
  没发现 (日志里已经搜得到原文了).
"""

from __future__ import annotations


class RedactError(Exception):
    """redact 包错误基类 (纯 Exception: 本包错误均为接线 / 配置期的编程错误)."""


class RedactConfigError(RedactError):
    """字段声明写坏了 (规则名不认识 / 路径不是字符串 / 路径为空).

    报错信息里带上**认识的规则名**, 让写错的人当场能改对 (与 `ModelConfigError`
    指向 .env.example 同一个用意).
    """
