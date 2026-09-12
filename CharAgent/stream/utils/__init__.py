"""stream 包支撑子包 (utils/): 静态支撑物, 不含行为类.

对齐 model/utils 与 tool/utils 惯例 —— 顶层 (bus.py) 放行为模块, 静态支撑
按主题收进子包:
- types.py   EventType (六类事件) / StreamEvent (事件对象 + to_dict) /
             EventSink (出口回调形态) / TERMINAL_TYPES /
             TOOL_RESULT_SUMMARY_LIMIT
- errors.py  异常语义: StreamError 基类 + EventSequenceError (状态机不变量)

模块内部 import 走具体模块路径 (stream.bus, stream.utils.types), 不绕包门面,
避免隐式循环依赖.

大白话版: 这里放 stream 包的「静态零件」(类型 / 常量 / 错误定义), 不含任何
行为逻辑 —— 行为都在上一层的 bus.py 里. 表与实现分开, 改契约不动实现.
"""

from __future__ import annotations
