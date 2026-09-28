"""eval 的支撑子包: 词汇 (types) + 异常 (errors).

与 `agent/utils` / `client/utils` 同一条组织纪律: 行为模块在顶层
(`runner` / `report` / `compare`), 静态零件收在这里.
"""

from __future__ import annotations

from CharAgent.eval.utils.errors import (
    EvalConfigError,
    EvalError,
    EvalStartupError,
)
from CharAgent.eval.utils.types import (
    RUN_OUTCOME_FOR_LOOP,
    CallRecord,
    EvalCase,
    Judgment,
    Metric,
    RunFacts,
    RunOutcome,
    run_outcome_of,
)

__all__ = [
    "RUN_OUTCOME_FOR_LOOP",
    "CallRecord",
    "EvalCase",
    "EvalConfigError",
    "EvalError",
    "EvalStartupError",
    "Judgment",
    "Metric",
    "RunFacts",
    "RunOutcome",
    "run_outcome_of",
]
