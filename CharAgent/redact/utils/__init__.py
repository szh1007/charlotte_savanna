"""redact 包支撑子包 (utils/): 静态支撑物, 不含行为类.

对齐 model/utils, tool/utils, stream/utils, hooks/utils, retry/utils 惯例 —— 顶层放
行为模块, 静态支撑按主题收进子包:
- errors.py  异常语义: RedactError 基类 + RedactConfigError (声明写坏了)

模块内部 import 走具体模块路径 (redact.rules, redact.utils.errors), 不绕包门面,
避免隐式循环依赖.

大白话版: 这里放 redact 包的「静态零件」(错误定义), 不含任何行为逻辑 —— 打码规则
在 rules.py, 字段路径匹配在 redactor.py, 协议在 protocol.py.
"""

from __future__ import annotations
