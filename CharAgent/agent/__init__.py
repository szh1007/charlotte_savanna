"""agent 运行时核心 (agent 包): 手写 agent loop + 循环防护 + 上下文压缩.

设计依据 (CharAgent/docs):
- difficulties #1/#2/#3/#7/#10/#62
- #3: 软限制 (max_turns / token 预算 / wall-clock) 轮后判定 vs kill switch
  (asyncio.Task.cancel) 即时打断, 语义对比是面试考点
- #7: 上下文压缩 = 账本 / 视图分离 (摘要 + 截断, 且不拆散 tool_calls 配对) ——
  配了 compactor 才生效, 不配时行为与从前逐字一样
- 每 Turn 结束产出完整消息历史快照; 配了 checkpoint saver 时同时落一帧快照
  (已接线: AgentLoop(saver=..., thread_id=...), 断点续跑走 resume())

结构总览 (顶层 = 行为模块, 门面导出; 对齐 model/tool 包惯例):
- loop.py         AgentLoop 行为主体: while 循环 (模型决策 → 并行工具 →
                  回填 → 截断/终止分支)
- guard.py        LoopGuard: 三种软限制 (max_turns / token 预算 / wall-clock)
- compaction.py   上下文压缩: 估算器 (TokenCounter) + 策略 (CompactionPolicy)
                  + 默认实现 (TrimAndSummarize) + 投影产物 (CompiledView)
- provider.py     业务接入点: RunContext (运行上下文) + ToolProvider (工具提供者)
- utils/          支撑子包: errors (错误族) / types (LoopOutcome /
                  TruncationStrategy / TurnRecord / LoopResult) / messages
                  (wire 消息构造 + 截断指令与摘要文案 + token 估算)

模块内部 import 走具体模块路径 (agent.loop, agent.utils.types), 不绕包
门面; 对外公共 API 统一由本文件 __all__ 导出.
"""

from __future__ import annotations

from CharAgent.agent.compaction import (
    CalibratedTokenCounter,
    CompactionPolicy,
    CompiledView,
    TokenCounter,
    TrimAndSummarize,
)
from CharAgent.agent.guard import LoopGuard
from CharAgent.agent.loop import AgentLoop
from CharAgent.agent.provider import RunContext, ToolProvider
from CharAgent.agent.utils.errors import (
    CompactionConfigError,
    GuardConfigError,
    LoopConfigError,
)
from CharAgent.agent.utils.types import (
    LoopOutcome,
    LoopResult,
    ToolCallFact,
    ToolCallOutcome,
    TraceSink,
    TruncationStrategy,
    TurnRecord,
)

__all__ = [
    "AgentLoop",
    "CalibratedTokenCounter",
    "CompactionConfigError",
    "CompactionPolicy",
    "CompiledView",
    "GuardConfigError",
    "LoopConfigError",
    "LoopGuard",
    "LoopOutcome",
    "LoopResult",
    "RunContext",
    "TokenCounter",
    "ToolCallFact",
    "ToolCallOutcome",
    "ToolProvider",
    "TraceSink",
    "TrimAndSummarize",
    "TruncationStrategy",
    "TurnRecord",
]
