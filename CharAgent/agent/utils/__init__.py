"""agent 包支撑子包 (utils/): 静态支撑物, 不含行为类.

对齐 model/utils 与 tool/utils 惯例 —— 顶层 (loop.py / guard.py) 放行为
模块, 静态支撑按主题收进子包, 便于审查与复用:
- errors.py    异常语义: AgentError 基类 + 各配置错误 (LoopConfigError /
                GuardConfigError)
- types.py     共享数据结构与枚举: LoopOutcome / TruncationStrategy /
                TurnRecord / LoopResult (AgentLoop 的输入输出类型)
                + LoopState (一次 run 的内存工作数据, run 与其分支方法之间
                传递用) + SERVER_INTERRUPTED (服务端中断的 finish_reason 集合)
- messages.py  wire 消息构造与面向模型的指令文案 (assistant/tool 回填消息
                构造 + 截断续写/精简指令) + count_tokens (单次响应 token 计量)
- events.py    事件载荷构造 (tool_call / tool_result, 含成功摘要截断) +
                终局出口 emit_terminal (从 LoopResult 派生恰好一个 final/error,
                03-api.md §2 定案) + 终局 error 文案 (code 取 LoopOutcome 值)

模块内部 import 走具体模块路径 (agent.loop, agent.utils.types 等), 不绕包
门面, 避免隐式循环依赖.

大白话版: 这里放主循环用到的「静态零件」(错误定义 / 共享类型 / 给模型看的
文案 / 给前端的事件措辞), 循环逻辑本身不在这里 —— 在上一层的 loop.py.
"""

from __future__ import annotations
