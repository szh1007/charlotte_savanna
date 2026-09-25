"""redact 包: 日志脱敏 —— 写出去之前先打码 (difficulties #26).

一句话理解: 日志 / 留痕里出现的敏感值, 在**写的那一步**就换成打码后的样子. 规矩
(手机号长什么样) 是框架给的, 名单 (我们家哪个字段是手机号) 是业务给的.

| 模块 | 一句话 |
|------|--------|
| `protocol.py` | `Redactor` 协议: 打码员的长相 (`redact_text` + `redact_fields`) |
| `rules.py` | 四条通用规则 (手机号 / 邮箱 / 身份证 / 银行卡) + `mask_text` |
| `redactor.py` | `RuleRedactor`: 规则 + 业务声明的字段路径 (按路径打码) |
| `utils/` | 静态支撑: `RedactError` / `RedactConfigError` |

**两个方法是分工, 不是重复** (#26 的原话是「按字段类型脱敏而非正则碰运气」):

- `redact_fields` 是**主手段** —— 有结构的数据按**声明的字段路径**打码, 知道它是什么
  就按它是什么打;
- `redact_text` 是**最后一道** —— 自由文本按形状认, 认不出就不认 (它不假装兜得住).

**放框架的理由**: 这四条形状是**跨业务**的 (换成 code agent 也还是手机号 / 邮箱 /
身份证 / 银行卡); 而「哪个字段敏感」是业务的知识, 所以由业务在装配处声明.

**为什么值得放在日志之前** (DESIGN #26 与 #38 的交汇点): 日志一旦落盘就会进
聚合 / 检索 / 备份, 事后擦不干净 —— 唯一的时机是**写之前**. 它管的是「打码之后再
出去」; 而「整条别出去」(把事件的载荷整个换掉) 是**业务那一层**的另一件事 ——
两件事, 两个用, 别混在一起.

**不做的事**: 不做「演示时把脱敏关掉」的开关 (ADR-0003 已否过一次: 那种开关迟早会
在某个不该开的场合被打开); 不做正则大杂烩 (只上确定知道形状的那四种).

大白话版: 这个包就是一把「打码的尺子」. 用的人只需记住两句话 —— 有结构的按字段名
打 (`redact_fields`), 一整句话按形状认 (`redact_text`).
"""

from __future__ import annotations

from CharAgent.redact.protocol import Redactor
from CharAgent.redact.redactor import WIPE, WIPED, RuleRedactor
from CharAgent.redact.rules import MASK_RULES, Mask, mask_text
from CharAgent.redact.utils.errors import RedactConfigError, RedactError

__all__ = [
    "MASK_RULES",
    "WIPE",
    "WIPED",
    "Mask",
    "RedactConfigError",
    "RedactError",
    "Redactor",
    "RuleRedactor",
    "mask_text",
]
