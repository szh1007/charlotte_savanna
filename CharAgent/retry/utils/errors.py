"""retry 包异常语义: 策略配置非法 / 幂等键非法 / 熔断拒发 (difficulties #13 #14 #17).

三类错误面向三种读者, 分开表达:

- RetryConfigError: 重试与熔断的**策略配置**非法 (上限非正 / 抖动越界 / 因子小于 1
  / 冷却非正等), 面向开发者, 构造期 fail fast —— 不把「永不重试」或「退避不收敛」
  的配置带到运行期去猜.
- IdempotencyKeyError: 幂等键非法 (空 / 超长 / 含白名单外字符), 面向调用方
  (#17) —— 客户端传入的键最终会变成下游存储的 key (Redis key / PG 主键) 与
  日志字段, 通配符、空白与控制字符必须挡在入口.
- CircuitOpenError: 熔断打开期间**这次请求没有发出去** (#14) —— 它不是「调用
  失败」而是「没调用」, 但对上层来说与「这条路走不通」同形, 见下方本类的说明.
"""

from __future__ import annotations

from CharAgent.model.utils.errors import ModelError


class RetryError(Exception):
    """retry 模块错误基类."""


class RetryConfigError(RetryError):
    """重试策略配置非法 (面向开发者, 构造期抛出)."""


class IdempotencyKeyError(RetryError):
    """幂等键非法 (面向调用方: 长度 / 字符集不符, #17)."""


class CircuitOpenError(ModelError):
    """熔断打开期间被拒 (difficulties #14): 这一次**没有发出请求**.

    为什么继承 ModelError 而不是只继承 RetryError: 对上层 (loop / CLI / server /
    业务降级) 来说, 「熔断」与「模型调用失败」是同一条路走不通 —— 既有的处理路径
    一条都不该变 (CLI 打一句人话、server 报失败帧、loop 的异常契约照旧). 于是它
    必须被既有的 `except ModelError` 接住, 而不是变成一条没人认识的异常从旁路溜走.

    为什么不可重试: 它带着 ModelError 的默认真 —— `retryable = False` (**不是**
    「未声明」, 而是明确声明成永久), 于是 RetryingChatModel 立刻放弃, 不在退避里
    空转: 闸开着的时候再试一百次还是同一个答案.
    """

    def __init__(self, message: str, *, name: str | None = None) -> None:
        """按「模型名 + 为什么不许过」造一条拒词.

        Args:
            message: 拒发的原因 (含冷却还剩多久 / 是阈值还是探测位占了).
            name: 哪个模型的闸 (None = 多个模型的闸一起说, 见 failover).
        """
        self.name = name
        super().__init__(message)
