"""日志脱敏: 本业务「哪些格子敏感」的那张名单, 外加把日志出口包一层 (issue 29).

一句话理解: 框架给的是**规矩** (手机号长什么样), 本模块给的是**名单** (我们这个
业务里哪个字段是手机号), 再把日志出口包一层 —— 写出去的每一行先过一遍打码.

**它与 `redaction.py` 是两件事, 不要合并** (ADR-0003 那次是「整条别出去」):

| 模块 | 管什么 | 做法 |
|------|--------|------|
| `redaction.py` | **事件**脱敏 (ADR-0003) | 载荷**整条**换成人话 |
| 本模块 | **日志**脱敏 (issue 29) | 打码**之后**再写出去 |

前者是「工具的参数原文与返回正文一个字节都不进浏览器」, 后者是「值还在, 但认不出来
了」—— 一个整条不留, 一个留下打码后的样子.


`redaction.py` 的 docstring 把范围写死在那张表上 (只管工具事件), 那句话照旧有效;
本模块一个字都不动它.

**名单为什么是「任何一层」(`**` 那个写法)**: 同一个字段在不同接口里包的层数不一样
(账户是一个平铺的 dict, 地址是列表里的每一项), 而**敏感不敏感跟包了几层没关系** ——
所以按名字认, 不管它在第几层. 名单里的每一格都是**确定的** (照
`app/minimall/serializers_agent.py` 的字段名抄的), 不是猜的.

**这一半现在还没有调用方**: 日志目前全是自由文本 (走的是 `redact_text`), 结构化日志
是 `DESIGN.md` 的 #38 (日志结构化 + 全链路关联) 那一片的事 —— 这张名单是**给那一刻
准备好的声明**, 今天的价值是「哪些字段敏感」这件事在代码里有一处写明的地方 (用例
验的是它按声明打码).

**不加演示开关**: ADR-0003 已经否过一次 (「演示时把敏感数据打开」的环境变量是安全
反模式), 本片同一条纪律 —— 名单是代码, 不是配置.

大白话版: 这里写着「手机号 / 余额 / 姓名 / 地址 / 支付密码这几样是我们家的敏感
字段」, 然后把日志出口包一层 —— 日志落盘那一刻它们就已经是星号了.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from CharAgent.redact import WIPE, Redactor, RuleRedactor

# 本业务的敏感字段名单 (字段名 → 怎么打).
#
# 三个名字照 `app/minimall/serializers_agent.py` 抄: 账户那条是平铺的
# (`phone` / `email` / `balance`), 地址那条是列表里的每一项 (`receiver_name` /
# `phone` / `detail`). 写法用 `**` 是因为层数随接口变, 而敏感与否不看层数.
#
# 两档处置:
#   - 手机号 / 邮箱走**规则** (留头留尾, 排查时仍分得清是不是同一个值);
#   - 其余整段 `WIPE` —— 余额、姓名、门牌这些没有「留哪几位」的说法, 留一位都是漏.
LOG_FIELDS: dict[str, str] = {
    "**.phone": "phone",
    "**.email": "email",
    "**.balance": WIPE,
    "**.receiver_name": WIPE,
    "**.detail": WIPE,
    # 支付密码: 今天**不在**任何载荷里 (ADR-0015 把它放进一次性载荷, 永不进 wire),
    # 这一条是提前声明 —— 哪天某个结构化日志真把它带进来, 打码立刻生效, 而不是
    # 等下一次事故才发现. 它也是全表唯一一条「一个字符都不许留」的.
    "**.payment_password": WIPE,
}


def build_redactor() -> RuleRedactor:
    """本业务的打码员: 通用规则 (框架) + 上面那张名单 (业务).

    形状与 `config.py` 里那几个 `*_from_env()` 同款 —— 装配处拿到的是一份**具体**
    的对象, 而不是「你自己去拼一个」.
    """
    return RuleRedactor(fields=LOG_FIELDS)


def redacting_writer(
    writer: Callable[[str], Any], redactor: Redactor
) -> Callable[[str], Any]:
    """把日志出口包一层: 每一行先打码, 再交给原来的出口.

    Args:
        writer: 原来的出口 (服务进程里是 `logger.info`, 命令行那里是 `print`).
        redactor: 打码员.

    Returns:
        Callable[[str], Any]: 形状与原来那个一样 (写一行字符串进去).

    Note:
        只包**日志那条出口** —— 命令行那个入口的出口是开发者自己的终端, 不经过
        这里 (与 ADR-0003 给命令行 `redact=False` 同一个道理: 威胁模型是「谁能看到
        这些留痕」, 而终端不落盘).
    """

    def write(line: str) -> Any:
        return writer(redactor.redact_text(line))

    return write


__all__ = ["LOG_FIELDS", "build_redactor", "redacting_writer"]
