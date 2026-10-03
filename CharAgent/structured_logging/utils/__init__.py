"""structured_logging 包支撑子包 (utils/): 静态支撑物, 不含行为类.

对齐 model/utils, tool/utils, stream/utils, hooks/utils, retry/utils, redact/utils
惯例 —— 顶层放行为模块, 静态支撑按主题收进子包:
- errors.py  异常语义: LoggingError 基类 + LoggingConfigError (配置写坏了)

模块内部 import 走具体模块路径 (logging.context, logging.utils.errors), 不绕包
门面, 避免隐式循环依赖.

大白话版: 这里放 structured_logging 包的「静态零件」(错误定义), 不含任何行为逻辑
—— 三个 id 在 context.py, 打码工序在 filter.py, 格式化在 formatter.py, 出口在
config.py.
"""

from __future__ import annotations
