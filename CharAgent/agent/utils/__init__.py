"""agent 包支撑子包 (utils/): 静态支撑物, 不含行为类.

对齐 model/utils 与 tool/utils 惯例 —— 顶层 (loop.py / guard.py) 放行为
模块, 静态支撑按主题收进子包, 便于审查与复用:
- errors.py    异常语义: AgentError 基类 + 各配置错误 (LoopConfigError /
                GuardConfigError)
- types.py     共享数据结构与枚举: LoopOutcome / TruncationStrategy /
                TurnRecord / LoopResult (AgentLoop 的输入输出类型)
                + SERVER_INTERRUPTED (服务端中断的 finish_reason 集合)
- messages.py  wire 消息构造与面向模型的指令文案 (assistant/tool 回填消息
                构造 + 截断续写/精简指令) + count_tokens (单次响应 token 计量)

模块内部 import 走具体模块路径 (agent.loop, agent.utils.types 等), 不绕包
门面, 避免隐式循环依赖.
"""

from __future__ import annotations
