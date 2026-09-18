"""hooks 包静态零件: hook 点枚举与失败记录.

对齐 agent/utils/types.py 的组织惯例 —— 行为 (HookRegistry 的注册与分发)
在 hooks/registry.py, 静态数据结构集中于此供 registry / 门面 / 消费方共享.

大白话版 (本文件 = 插座规格表):
- 5 个「喊一声」的时机分别叫什么: 每轮开工前 (before_turn) / 每轮收工后
  (after_turn) / 问模型前后 (on_model_call) / 每个工具跑完 (on_tool_executed)
  / 每声喊话之后 (on_event).
- 插头长什么样: 普通函数或 async 函数都行 (HookFn), 载荷以关键字参数传入.
- 顺手记两件事: 问模型分「发出前」和「收到后」两个 phase (ModelCallPhase);
  坏插头记成一条记录 (HookFailure), 不静默丢.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

# hook 函数形态: 同步或异步都接受 (registry.fire 统一 await 到位).
# 载荷以关键字参数传递, 各点的载荷清单见 registry.HookRegistry docstring.
type HookFn = Callable[..., Awaitable[None] | None]


class HookPoint(StrEnum):
    """五个生命周期 hook 点 (扩展点, P0 落地骨架; 空注册零开销).

    触发时机与载荷见 registry.HookRegistry docstring. P2 模块 (memory / cost /
    observability) 经此挂载, 核心零 import P2.
    """

    BEFORE_TURN = "before_turn"  # 每 Turn 模型调用前 (memory 注入记忆)
    AFTER_TURN = "after_turn"  # 每 Turn 结束后 (memory 落库 / cost 记账)
    ON_MODEL_CALL = "on_model_call"  # 模型请求发出前 / 响应返回后 (两个 phase)
    ON_TOOL_EXECUTED = "on_tool_executed"  # 每条工具执行完成 (指标 / 审计)
    ON_EVENT = "on_event"  # 每个 StreamEvent 分发后 (sink 之后, trace 采集)


class ModelCallPhase(StrEnum):
    """ON_MODEL_CALL 的两次触发 (载荷字段 phase 的取值).

    模型调用在 hook 视角是一个区间: 请求发出前 (可观测 / 计量准备) 与响应返回后
    (记账 / 采样参数核对) 语义不同, 故用同一 hook 点分两个 phase 触发.
    """

    BEFORE = "before"  # 请求发出前
    AFTER = "after"  # 响应返回后


@dataclass(slots=True)
class HookFailure:
    """被隔离的 hook 异常记录 (扩展点出错不拖垮核心, 但必须留痕不静默).

    hook 抛出的 Exception 由 HookRegistry 捕获后记入 registry.failures ——
    调用方 (测试 / CLI / P1 server) 可检查; 结构化日志接上后由此转 logger.
    CancelledError 属 BaseException, 不入此记录 (直接向上传播,
    插件不得挡住 kill switch).
    """

    point: HookPoint
    hook: HookFn
    error: BaseException
