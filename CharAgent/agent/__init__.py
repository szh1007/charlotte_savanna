"""agent 运行时核心 (agent 包): 手写 agent loop + 循环防护 (issue 04).

设计依据 (CharAgent/docs):
- difficulties #1/#2/#3/#10/#62, design/01-architecture.md §3 主循环伪代码
- #3: 软限制 (max_turns / token 预算 / wall-clock) 轮后判定 vs kill switch
  (asyncio.Task.cancel) 即时打断, 语义对比是面试考点
- 每 Turn 结束产出完整消息历史快照, 供 P0-6 checkpoint 落盘 (issue 07)

结构总览 (顶层 = 行为模块, 门面导出; 对齐 model/tool 包惯例):
- loop.py         AgentLoop 行为主体: while 循环 (模型决策 → 并行工具 →
                  回填 → 截断/终止分支)
- guard.py        LoopGuard: 三种软限制 (max_turns / token 预算 / wall-clock)
- utils/          支撑子包: errors (错误族) / types (LoopOutcome /
                  TruncationStrategy / TurnRecord / LoopResult) / messages
                  (wire 消息构造 + 截断指令文案)

模块内部 import 走具体模块路径 (agent.loop, agent.utils.types), 不绕包
门面; 对外公共 API 统一由本文件 __all__ 导出.
"""

from __future__ import annotations

from CharAgent.agent.guard import LoopGuard
from CharAgent.agent.loop import AgentLoop
from CharAgent.agent.utils.errors import GuardConfigError, LoopConfigError
from CharAgent.agent.utils.types import (
    LoopOutcome,
    LoopResult,
    TruncationStrategy,
    TurnRecord,
)

__all__ = [
    "AgentLoop",
    "GuardConfigError",
    "LoopConfigError",
    "LoopGuard",
    "LoopOutcome",
    "LoopResult",
    "TruncationStrategy",
    "TurnRecord",
]
