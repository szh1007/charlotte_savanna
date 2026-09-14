"""checkpoint 包异常语义: 五类错误, 各面向一种读者 (difficulties #5 / ADR-0002).

一句话理解: 「存快照」这件事会以五种不同的方式出问题, 而每种问题的「该找谁
修」都不一样, 所以分成五个类, 不混成一个 Exception —— 报错信息写清是哪一类,
读代码的人一眼就知道该去改配置、改数据还是查服务.

- CheckpointConfigError: 配置/参数写错了 (后端名不认识 / 缺连接串 / TTL 是
  负数 / thread_id 不合法). 面向开发者, 在**造 saver 或造快照的那一刻**就报错
  (fail fast), 不等到真去存才发现.
- CheckpointSerializationError: 序列化失败 —— 要么往快照里塞了装不进 JSON 的
  东西 (比如一个打开的文件对象), 要么反过来: 存储里那段文本根本不是合法 JSON.
  面向「往快照里放东西」的调用方 (#5: 快照要能写成文本, 就只能放文本能表达的
  东西; 放不了的类型需要注册翻译器, 见 serialization.py).
- CheckpointMigrationError: 老快照升级失败, 或快照来自「比本代码更新的版本」
  (未来的格式, 现在的代码读不懂) —— 面向升级/运维场景 (#5 向前兼容).
- CheckpointCapabilityError: 让这个存储做它做不到的事 (Redis 只有最新一帧,
  却被要求翻历史) —— 面向调用方, 提示「换个存储」.
- CheckpointStorageError: 存储自己出问题了 (连不上 / SQL 报错) —— 底层异常被
  包装在这里并保留原因 (raise ... from exc), 供排查.

对齐 retry/utils/errors.py 的组织: 一个基类 + 若干子类, 每个子类说明自己的语义.
"""

from __future__ import annotations


class CheckpointError(Exception):
    """checkpoint 模块错误基类 (归到这一类, 调用方一次 except 就能兜住)."""


class CheckpointConfigError(CheckpointError):
    """配置或参数非法 (后端名未知 / 缺连接串 / 标识别符不合法), 构造期抛出."""


class CheckpointSerializationError(CheckpointError):
    """序列化失败: 对象装不进 JSON, 或存着的文本解不出来 (#5)."""


class CheckpointMigrationError(CheckpointError):
    """schema 版本迁移失败: 缺迁移函数, 或快照版本比本代码新 (#5)."""


class CheckpointCapabilityError(CheckpointError):
    """当前存储不支持该操作 (Redis 只存最新一帧, 不能翻历史 / 按 id 取)."""


class CheckpointStorageError(CheckpointError):
    """存储后端自身出错 (连接失败 / SQL 失败), 原始异常经 from 保留."""
