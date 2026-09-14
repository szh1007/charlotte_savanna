"""db 包异常语义: 三类错误, 各面向一种读者 (difficulties #12).

一句话理解: 「把业务数据存进库」这件事会以三种不同的方式出问题, 而每种问题的
「该找谁修」都不一样, 所以分成三个类, 不混成一个 Exception —— 报错写清是哪
一类, 读代码的人一眼就知道该去改配置、改数据还是查服务.

- DataConfigError: 配置/参数写错了 (连接信息不全 / 标识符不合法 / 时间倒着写).
  面向开发者, 在**造对象或连库的那一刻**就报错 (fail fast), 不等到真去存才发.
- InvalidTransitionError: 状态机不允许这么走 (让一个已经跑完的 run 变回「运行
  中」). 面向**状态推进的调用方** —— 这类错误通常意味着调用方漏判了一个分支,
  而不是数据坏了; 报错信息里必须带上「从哪到哪」与「允许去哪儿」.
- DataStoreError: 库自己出问题了 (连不上 / SQL 报错 / 库里的值读不出来).
  底层异常被包装在这里并保留原因 (raise ... from exc), 供排查.

为什么不像 checkpoint 那样再加「序列化类」错误: 那边要处理「装不进 JSON 的
对象」与「老格式读回来」(schema_version + 迁移), 本包的实体字段都是明确的
标量与 JSON 列, 序列化不是独立问题.

对齐 checkpoint/utils/errors.py 与 retry/utils/errors.py 的组织: 一个基类 +
若干子类, 每个子类说明自己的语义.
"""

from __future__ import annotations


class DbError(Exception):
    """db 模块错误基类 (调用方一次 except 就能兜住这一包).

    名字带 db 前缀是必须的: `model/utils/errors.py` 里已经有一个
    `ModelError` (LLM 模型层的), 两者同名不同类 —— 那样 `except
    ModelError` 既兜不住模型层的错也兜不住 db 层的错, 而代码看起来
    毫无问题 (2026-09-14 审计发现并改名).
    """


class DataConfigError(DbError):
    """配置或参数非法 (缺连接信息 / 标识符不合法 / 时间戳顺序不对), 构造期抛出."""


class InvalidTransitionError(DbError):
    """状态机不允许这次迁移 (见 state.py 的合法迁移表)."""

    def __init__(self, from_status: str, to_status: str, allowed: str) -> None:
        """记下「从哪到哪」与「本来能去哪儿」, 排查时不用再去翻状态机表.

        Args:
            from_status: 当前状态.
            to_status: 想迁到的状态.
            allowed: 从当前状态出发本来允许去哪些 (已拼好的可读文本).
        """
        self.from_status = from_status
        self.to_status = to_status
        self.allowed = allowed
        super().__init__(
            f"状态不能从 {from_status} 迁到 {to_status}; "
            f"从 {from_status} 可以去的只有: {allowed}"
        )


class DataStoreError(DbError):
    """数据库自身出错 (连接失败 / SQL 失败 / 行里的值读不出来), 原始异常经 from 保留."""
