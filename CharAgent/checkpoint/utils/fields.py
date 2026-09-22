"""从解好的 JSON 结构里按字段取值 + 校验 (serialization.py 的取值工具).

一句话理解: 快照从存储里读回来时是一堆**来路不明的数据** (可能是别的版本写的,
也可能被人手改过). 直接 `payload["turn_count"]` 取值, 一旦字段缺失或类型不对,
报出来的会是 `KeyError` / 后面某处莫名其妙的 `TypeError`; 这里统一改成「说清
哪个字段、本来想要什么、实际拿到什么」的错误, 排查时一眼就知道问题在哪.

每个函数三个约定 (读代码只需记住这三条):
1. 字段缺失 -> 返回默认值 (缺字段往往是「老版本没这个概念」, 填默认就是当时的
   事实 —— 与 migrations.py 的分工: 那边管结构, 这边管取值)
2. 字段在但类型不对 -> 抛 CheckpointSerializationError, 错误信息给出实际类型
3. 不认识的字段 -> 忽略 (向前兼容: 新版本多写的字段, 老代码读的时候当没看见,
   不报错. 这条也是「只加不改」的基础)
"""

from __future__ import annotations

from typing import Any

from CharAgent.checkpoint.utils.errors import CheckpointSerializationError


def read_int(payload: dict[str, Any], key: str, *, default: int = 0) -> int:
    """取一个整数计数 (缺失给默认值; 布尔值不算整数, 避免 True 被当成 1)."""
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckpointSerializationError(
            f"字段 {key!r} 应当是整数, 实际为 {type(value).__name__}: {value!r}"
        )
    return value


def read_optional_int(payload: dict[str, Any], key: str) -> int | None:
    """取一个可空整数 (缺失或 null 都给 None; 类型不对报错).

    与 `read_int` 的区别只在「字段缺失」这一种情况上的语义, 而那个区别是有意的:

    - `read_int` 缺失 -> 0. 用于**计数器** (轮数 / 截断次数): 那时 0 就是当时的
      事实 —— 老版本没有这个字段, 而它确实是零.
    - `read_optional_int` 缺失 -> None. 用于**上游上报的用量**: None 说的是
      「这一次没拿到这个值」(上游没给 usage / 没这个分量), 与 0 (给过、值就是
      零) 是两回事. 成本归因里这两者结论相反 (「真的没命中缓存」vs「这次没拿到
      缓存数据」), 混起来会让报表撒谎.
    """
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckpointSerializationError(
            f"字段 {key!r} 应当是整数或 null, 实际为 {type(value).__name__}: {value!r}"
        )
    return value


def read_float(payload: dict[str, Any], key: str, *, default: float = 0.0) -> float:
    """取一个浮点量 (毫秒这类; 整数也接受 —— JSON 里 5 与 5.0 常常不分)."""
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CheckpointSerializationError(
            f"字段 {key!r} 应当是数字, 实际为 {type(value).__name__}: {value!r}"
        )
    return float(value)


def read_str(payload: dict[str, Any], key: str) -> str:
    """取一个必填字符串 (缺失或类型不对都报错: 这种字段没有合理的默认值)."""
    if key not in payload:
        raise CheckpointSerializationError(f"快照缺少必填字段 {key!r}")
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise CheckpointSerializationError(
            f"字段 {key!r} 应当是非空字符串, 实际为 {type(value).__name__}: {value!r}"
        )
    return value


def read_optional_str(payload: dict[str, Any], key: str) -> str | None:
    """取一个可空字符串 (缺失或 null 都给 None; 类型不对报错)."""
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CheckpointSerializationError(
            f"字段 {key!r} 应当是字符串或 null, 实际为 "
            f"{type(value).__name__}: {value!r}"
        )
    return value


def read_str_list(payload: dict[str, Any], key: str) -> list[str]:
    """取一个字符串列表 (缺失给空表; 元素类型不对报错)."""
    value = payload.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CheckpointSerializationError(
            f"字段 {key!r} 应当是字符串列表, 实际为 {value!r}"
        )
    return list(value)
