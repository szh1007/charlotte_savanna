"""通用打码规则 (difficulties #26): 四种「确定知道形状」的值 + 一段文字的整体打码.

一句话理解: 日志进之前先把这几类值换成打码后的样子 —— 认得出来的打, 认不出来的
如实留着 (不假装).

| 规则名 | 形状 | 打码后 |
|--------|------|--------|
| `id_card` | 18 位身份证 (末位可为 X) | `110101199003071234` → `1101**********1234` |
| `bank_card` | 16-19 位银行卡 | `6222021234567890123` → `6222***********0123` |
| `phone` | 11 位手机号 (1 开头, 第二位 3-9) | `13800000003` → `138****0003` |
| `email` | 邮箱 | `buyer3@example.com` → `b*****@example.com` |

顺序就是上表的顺序 (先长后短): 18 位的身份证同时也是「16-19 位数字」, 所以先认它;
而两者留头留尾都是 4 位, 于是**就算认错, 打出来的样子也一样** —— 规则之间不会互相
打架 (重叠的形状打出来的码相同, 这是有意挑的参数).

三条纪律:

1. **只上确定知道形状的**. 手机号 11 位 / 邮箱 / 身份证 / 银行卡是**跨业务**的形状
   (#26 的判据: 换成 code agent 也还是这几样), 所以放框架. 订单号那种「本项目自己
   发的编号」**不猜** —— 它的形状是实现细节, 猜错了会误伤真数据 (本项目真机上那个
   订单号是 24 位纯数字, 下面四条规则一条都不碰它). 订单号不进日志靠**不打它**
   (业务那侧少记一处, 比猜一条规则靠得住), 不靠规则.
2. **数字的边界用 `(?<!\\d)` / `(?!\\d)`, 不用 `\\b`**. 中文在 Python 的 `re` 里也算
   `\\w`, 于是 `手机号13800000003` 里汉字与数字之间**没有**词边界 —— 用 `\\b` 写的
   规则在中文日志里会静默失效, 而中文日志恰恰是本项目的主场.
3. **打码留「形状」不留「值」**: 留头留尾是为了让排查的人分得清两条日志说的是不是
   同一个值 (`138****0003` 与 `138****0005` 是两个人), 而不是为了好看. 位数本来就是
   公开的格式信息 (手机号就是 11 位), 所以按原长度补 `*` 不额外泄漏什么.

大白话版: 这里就四把「认形状的尺子」—— 手机号 / 邮箱 / 身份证 / 银行卡, 认出来就
把中间那截换成星号. 认不出来的 (订单号 / 人名 / 短号) 一个字都不动.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# 18 位身份证: 前 17 位数字 + 末位数字或 X (校验位)
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
# 16-19 位银行卡; 前后那一对顾盼见模块 docstring 第 2 条
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
# 11 位手机号: 1 开头, 第二位 3-9 (虚拟运营商号段也在内)
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 邮箱: 形状写宽松一点够用 —— 宁可不认 (漏一个), 不要误伤带 @ 的文案
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _middle(head: int, tail: int) -> Callable[[re.Match[str]], str]:
    """造一个「留头 N 位 + 留尾 M 位, 中间全换 `*`」的改写器 (位数照旧).

    Note:
        短到没有中间可打时 (声明的字段值比头尾还短) **整段打掉**, 不退化成原文 ——
        字段声明是「这一格按手机号打」这种**确定的话**, 那就不该有「它太短所以我
        照原样留着」的出口.
    """

    def replace(match: re.Match[str]) -> str:
        text = match.group()
        if len(text) <= head + tail:
            return "*" * len(text)
        middle = len(text) - head - tail
        return f"{text[:head]}{'*' * middle}{text[len(text) - tail :]}"

    return replace


def _mask_email(match: re.Match[str]) -> str:
    """邮箱: 本地部分留首字符, 域名照旧 (排查时要看是哪个域来的)."""
    local, _, domain = match.group().partition("@")
    return f"{local[:1]}{'*' * max(len(local) - 1, 1)}@{domain}"


@dataclass(frozen=True, slots=True)
class Mask:
    """一条打码规则: 什么形状 + 换成什么样.

    Args:
        name: 规则名 (字段声明里用的就是这个名字).
        pattern: 认形状的正则.
        replace: 命中之后怎么改写 (接 `re.Match`, 返回打码后的字符串).
    """

    name: str
    pattern: re.Pattern[str]
    replace: Callable[[re.Match[str]], str]

    def apply(self, text: str) -> str:
        """把这段文字里**所有**命中这条规则的地方打码."""
        return self.pattern.sub(self.replace, text)


# 四把尺子 (顺序即应用顺序, 见模块 docstring 第 2 段)
MASK_RULES: dict[str, Mask] = {
    "id_card": Mask("id_card", _ID_CARD, _middle(4, 4)),
    "bank_card": Mask("bank_card", _BANK_CARD, _middle(4, 4)),
    "phone": Mask("phone", _PHONE, _middle(3, 4)),
    "email": Mask("email", _EMAIL, _mask_email),
}


def mask_text(text: str) -> str:
    """把一段**自由文本**里认得出的敏感值全部打码 (认不出的如实留着).

    **这是最后一道, 不假装兜得住**: 没有字段知识的地方, 正则就是在按形状猜 ——
    所以它只认上面那四种形状, 别的 (订单号 / 人名 / 短号) 一个字都不动.

    想按字段名精确打码 (「知道它是手机号, 就按手机号打」) 该走
    `RuleRedactor.redact_fields`.
    """
    for mask in MASK_RULES.values():
        text = mask.apply(text)
    return text
