"""幂等键与幂等存储 (issue 06 / difficulties #17, 关联 #13).

一句话理解: 重试让「同一件事」可能被执行两次 —— 查询两次没关系, 退款两次
就是事故. 幂等键就是给每件事发的**唯一凭证**: 动作执行前先拿凭证认领, 重复
请求看到凭证已被用过, 直接返回上次的结果, 不再执行第二遍.

两个零件:

1. ``IdempotencyKey`` —— 凭证本身 (生成 + 校验), 见类 docstring;
2. ``IdempotencyStore`` 协议 (claim / complete / release) 与进程内实现
   ``InMemoryIdempotencyStore`` —— 凭证的登记簿: 认领 (CLAIMED) → 在途
   (IN_PROGRESS) → 完成 (COMPLETED, 带结果); 动作失败要 release 放行,
   否则该键永久卡在「在途」, 后续合法重试全被挡住.

P0 的边界 (P1-4 补齐): 进程内 dict, **无 TTL / 无持久化 / 跨实例失效**,
也没有 Saga 补偿 —— 生产形态是 Redis SETNX + TTL 续租 + owner 校验, 见
roadmap P1-4.

大白话版: 幂等键 = 快递单号, 存储 = 单号台账. 同一单号来两次: 第一次登记
「在办」并真的去办, 办完标「已办」并记结果; 第二次来直接告诉你「已办, 结果
是……」, 而不是再办一遍. 办砸了要把「在办」撤掉, 否则这单号就废了.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from CharAgent.retry.utils.errors import IdempotencyKeyError
from CharAgent.retry.utils.types import ClaimResult, ClaimStatus

# 键的长度上限: 下游存储 key (Redis / PG 主键) 与日志字段都够用
MAX_KEY_LENGTH = 255
# 允许字符白名单: 字母数字开头 + 字母数字与 . _ : - —— 禁空白 / 控制字符 /
# 通配符 / 路径分隔符 (这个值会变成存储 key、URL 片段与日志字段, 必须防注入)
KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """幂等键 (#17): 一次真实动作的唯一凭证 (构造即校验).

    客户端传入的键必须先过这道关再进存储层 —— 长度 1~255, 字符集
    ``[A-Za-z0-9._:-]`` 且首字符为字母或数字.

    Raises:
        IdempotencyKeyError: 键为空 / 超长 / 含白名单外字符.
    """

    value: str

    def __post_init__(self) -> None:
        """构造期校验 (fail fast): 非法键不进入任何存储或日志."""
        if (
            not self.value
            or len(self.value) > MAX_KEY_LENGTH
            or not KEY_PATTERN.match(self.value)
        ):
            raise IdempotencyKeyError(
                f"幂等键非法 (长度 1~{MAX_KEY_LENGTH}, 允许字符 [A-Za-z0-9._:-], "
                f"首字符须为字母或数字): 实际长度 {len(self.value)}, "
                f"值 {_preview(self.value)}"
            )

    @classmethod
    def generate(cls) -> IdempotencyKey:
        """生成一个随机幂等键 (uuid4 十六进制, 32 字符) —— 服务端内部动作用."""
        return cls(uuid.uuid4().hex)

    @classmethod
    def parse(cls, raw: str | IdempotencyKey) -> IdempotencyKey:
        """解析幂等键: 字符串走校验, 已是 IdempotencyKey 的原样返回 (幂等)."""
        return raw if isinstance(raw, IdempotencyKey) else cls(raw)

    def __str__(self) -> str:
        """裸值 (可直接当 dict key / 日志字段 / 存储键)."""
        return self.value


class IdempotencyStore(Protocol):
    """幂等记录存储协议 (SPI): P0 进程内实现, P1-4 补 Redis / PG 实现.

    三个动作构成一次鉴权往返: 动作执行**前** claim (拿到才执行), 成功后
    complete (记结果), 失败后 release (放行合法重试).
    """

    async def claim(self, key: IdempotencyKey) -> ClaimResult:
        """认领: CLAIMED = 首次且可执行; IN_PROGRESS = 有人在做; COMPLETED = 取结果."""
        ...

    async def complete(self, key: IdempotencyKey, result: Any = None) -> None:
        """登记完成与结果 (动作成功收尾)."""
        ...

    async def release(self, key: IdempotencyKey) -> None:
        """释放认领 (动作失败): 后续请求可重新认领, 否则该键永久卡在在途."""
        ...


@dataclass(slots=True)
class _Record:
    """登记簿内部条目: 在途 (IN_PROGRESS) 或已完成 (COMPLETED, 带结果)."""

    status: ClaimStatus
    result: Any = None


class InMemoryIdempotencyStore:
    """进程内幂等存储 (#17 最小实现; 单进程 / 单事件循环).

    并发说明: 单事件循环内 dict 读改写之间没有 await, 天然原子 —— 同一 key
    的并发认领在进程内不会同时拿到执行权. **跨进程不适用** (多实例要靠
    P1-4 的 Redis SETNX).

    边界 (P1-4 补齐): 无 TTL / 无持久化 (进程重启即遗忘) / 无 Saga 补偿.
    """

    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}

    async def claim(self, key: IdempotencyKey) -> ClaimResult:
        """认领凭证 (#17): 首次拿到执行权, 重复请求被挡回在途 / 已完成状态."""
        record = self._records.get(key.value)
        if record is None:  # 首次认领拿到执行权
            self._records[key.value] = _Record(status=ClaimStatus.IN_PROGRESS)
            return ClaimResult(status=ClaimStatus.CLAIMED)
        if record.status is ClaimStatus.COMPLETED:  # 已完成, 认领直接返回结果
            return ClaimResult(status=ClaimStatus.COMPLETED, result=record.result)
        return ClaimResult(status=ClaimStatus.IN_PROGRESS)  # 重复认领被拒绝 - 在途

    async def complete(self, key: IdempotencyKey, result: Any = None) -> None:
        """登记完成与结果; 无认领记录时直接补登 (动作已落地但记录缺失的兜底)."""
        self._records[key.value] = _Record(status=ClaimStatus.COMPLETED, result=result)

    async def release(self, key: IdempotencyKey) -> None:
        """释放**在途**认领 (动作失败, 允许后续合法重试).

        已完成的记录不动 —— 结果不能被后到来的失败抹掉 (#17 幂等语义的核心).
        """
        record = self._records.get(key.value)
        if record is not None and record.status is ClaimStatus.IN_PROGRESS:
            del self._records[key.value]  # 如果动作失败, 及时释放, 防止永久卡在「在途」


def _preview(value: str, limit: int = 40) -> str:
    """键预览 (超长截断): 错误消息不携带调用方可控的任意长度输入 (日志安全).

    实际长度由调用方另行给出, 故截断标记只说明「此处省略」.
    """
    if len(value) <= limit:
        return repr(value)
    return f"{value[:limit]!r}... (已截断)"
