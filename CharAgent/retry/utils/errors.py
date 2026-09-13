"""retry 包异常语义: 策略配置非法 vs 幂等键非法 (difficulties #13 / #17).

两类错误面向两种读者, 分开表达:

- RetryConfigError: 重试策略配置非法 (上限非正 / 抖动越界 / 因子小于 1 等),
  面向开发者, 构造期 fail fast —— 不把「永不重试」或「退避不收敛」的配置
  带到运行期去猜.
- IdempotencyKeyError: 幂等键非法 (空 / 超长 / 含白名单外字符), 面向调用方
  (#17) —— 客户端传入的键最终会变成下游存储的 key (Redis key / PG 主键) 与
  日志字段, 通配符、空白与控制字符必须挡在入口.
"""

from __future__ import annotations


class RetryError(Exception):
    """retry 模块错误基类."""


class RetryConfigError(RetryError):
    """重试策略配置非法 (面向开发者, 构造期抛出)."""


class IdempotencyKeyError(RetryError):
    """幂等键非法 (面向调用方: 长度 / 字符集不符, #17)."""
