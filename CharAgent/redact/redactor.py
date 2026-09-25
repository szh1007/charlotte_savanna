"""按声明的字段路径打码 (`Redactor` 协议的主手段, difficulties #26).

一句话理解: 业务交出「哪些格子是敏感的」这张表 (手机号 → 手机号规则, 支付密码 →
整段抹掉), 框架照着打 —— **知道它是什么, 就按它是什么打**, 不靠正则碰运气.

**路径怎么写** (与 glob 同义, 不自创第三种含义):

| 写法 | 意思 |
|------|------|
| `phone` | 顶层那个 `phone` |
| `customer.phone` | 再往里一层 |
| `addresses.*.phone` | 列表 / 字典里的每一个元素 (一层) |
| `**.payment_password` | **任意层**里名字叫它的那一个 (它自己那一层也算) |

**最后一段必须是字段名** (通配符只出现在中间): 通配符是用来**找名字**的, 它自己指不
出一格来 —— `"a.**"` 这种写法在构造期就报 (放过去会在打码那一刻崩, 而不是在装配期说清).

**三条行为约定** (都有用例钉着):

1. **不改原件**: `redact_fields` 返回一棵**新**树, 只有沿途该改的分支被拷贝; 递进来
   的那个 dict 一个字节都不动 (它常常是正要落库 / 正要发给别人的原始数据).
2. **值不是字符串也打得住**: 数字手机号 `13800000003` 按它的字符串形式打码 (结果是
   `"138****0003"`). 日志这一格要的是「打过码了」, 类型不是它关心的事.
3. **`WIPE` 不看形状**: 声明的值是 `WIPE` 就整段换成 `WIPED`, 不管里面是什么
   (dict / 列表 / 数字都行) —— 支付密码这种「一个字符都不该留」的值走这条.

**声明写错在构造期就报** (规则名不认识 / 路径为空 / 路径里有空段): 脱敏失效的表现
不是报错而是**静默地漏**, 所以认不出的规则名必须在装配那一刻就拦住 (见
`utils/errors.py` 的说明).

大白话版: 业务说「`phone` 这一格是手机号、`payment_password` 这一格整个抹掉」,
框架照着改一份**副本**给你; 名单里有错别字, 当场报错.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from CharAgent.redact.rules import MASK_RULES, mask_text
from CharAgent.redact.utils.errors import RedactConfigError

# 声明里的两个特殊值 (见模块 docstring 第 3 条):
#   WIPE  —— 写在**声明**里的: 这一格整段抹掉
#   WIPED —— 真**写进数据**里的那串文字
WIPE = "<wipe>"
WIPED = "[已抹除]"

# 一条路径拆成的段 (`"addresses.*.phone"` -> `("addresses", "*", "phone")`)
Segment = tuple[str, ...]

# 两个通配符 (与 glob 同义: `*` 一层 / `**` 任意层)
_WILDCARDS = frozenset({"*", "**"})


class RuleRedactor:
    """通用规则 + 业务声明的字段路径 (「按路径打码」那一半).

    Args:
        fields: 字段路径 → 规则名 (或 `WIPE`), 形如
            `{"phone": "phone", "**.payment_password": WIPE}`. 不填 = 没有声明,
            那一半什么也不做 (自由文本那一半照常).

    Raises:
        RedactConfigError: 规则名不认识 / 路径不是字符串 / 路径为空 / 路径里有空段.
    """

    def __init__(self, fields: Mapping[str, str] | None = None) -> None:
        declared: list[tuple[Segment, str]] = []
        for path, rule in (fields or {}).items():
            declared.append((self._parse(path), _require_rule(rule)))
        self._declared: tuple[tuple[Segment, str], ...] = tuple(declared)

    def redact_text(self, text: str) -> str:
        """自由文本: 走通用规则 (`rules.mask_text`, 承认它是最后一道)."""
        return mask_text(text)

    def redact_fields(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """按声明打码, 返回**新**字典 (递进来的那一份不动)."""
        out: Any = dict(data)
        for segments, rule in self._declared:
            out = _apply(out, segments, rule)
        return out

    @staticmethod
    def _parse(path: str) -> Segment:
        """把一条路径拆成段 (只认点号分层, 不做别的语法).

        Raises:
            RedactConfigError: 路径不是非空字符串 / 拆出来有空段 (多打了个点) /
                整条路径只有通配符.
        """
        if not isinstance(path, str) or not path.strip():
            raise RedactConfigError(f"字段路径要是非空字符串, 实际: {path!r}")
        segments = tuple(path.split("."))
        if any(not segment for segment in segments):
            raise RedactConfigError(f"字段路径里有空段 (多打了个点?): {path!r}")
        if segments[-1] in _WILDCARDS:
            # 通配符是用来**找名字**的 (名字在第几层不确定), 而它自己指不出一格来 ——
            # 路径最后一段必须是那个字段名. 放过去的话 `{"a.**": WIPE}` 走到「整棵
            # 子树先被抹成一个字符串、再拿它当字典改」那条路上, 报一个看不懂的
            # TypeError, 而不是在装配那一刻说清声明写错了.
            raise RedactConfigError(
                f"字段路径的最后一段不能是通配符 (指不出是哪一格): {path!r}"
            )
        return segments


def _require_rule(rule: str) -> str:
    """校验一个声明用的规则名 (报错时把认识的名字列出来).

    Raises:
        RedactConfigError: 不是字符串, 也不是 `WIPE`, 也不在规则表里.
    """
    if rule == WIPE:
        return WIPE
    if not isinstance(rule, str) or rule not in MASK_RULES:
        known = ", ".join(MASK_RULES)
        raise RedactConfigError(
            f"不认识的规则名: {rule!r} (认识的: {known}; 整段抹掉写 WIPE)"
        )
    return rule


def _apply(value: Any, segments: Segment, rule: str) -> Any:
    """把一条声明应用到 `value` 上, 返回**新**值 (只沿这一条路拷贝).

    Note:
        碰不到的地方原样返回 (连拷贝都不做) —— 于是 `redact_fields` 只在真改过的那
        条路上产生新对象, 其余分支与输入**共享**, 大 payload 也不会被整体深拷一遍.
    """
    if not segments:
        return _masked(rule, value)
    head, rest = segments[0], segments[1:]
    if isinstance(value, Mapping):
        out = dict(value)
        if head == "*":
            for key in list(out):
                out[key] = _apply(out[key], rest, rule)
        elif head == "**":
            # 任意层: 先当「这里就是终点」试一次, 再往里多包一层继续找 (0 层也算)
            out = _apply(out, rest, rule)
            for key in list(out):
                out[key] = _apply(out[key], segments, rule)
        elif head in out:
            out[head] = _apply(out[head], rest, rule)
        return out
    if isinstance(value, list):
        if head == "*":
            return [_apply(item, rest, rule) for item in value]
        if head == "**":
            return [_apply(item, segments, rule) for item in _apply(value, rest, rule)]
    return value


def _masked(rule: str, value: Any) -> Any:
    """按规则改写一个值 (WIPE 不看形状, 其余走规则表)."""
    if rule == WIPE:
        return WIPED
    return MASK_RULES[rule].apply(str(value))
