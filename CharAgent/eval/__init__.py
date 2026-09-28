"""eval 包: 离线跑分 —— 一批题跑 N 次, 判一遍, 出一份报告 (difficulties #58/#62/#63).

一句话理解: **测试回答「代码对不对」, 这一包回答「输出好不好」** (#63 那条分界).
测试用假大脑 (MockLLM) 验确定的接线, 跑分用真模型验一个概率性的东西 —— 所以它
不看单次通过, 而是**跑 N 次看通过率** (#62), 且判据只做规则判定 (L4 定: 不用
LLM-as-judge, 那会引入位置 / 长度 / 自我偏好三种偏差, #58).

## 框架管流程, 业务管两件事

| 谁 | 管什么 |
|----|--------|
| 框架 (本包) | 跑 N 题 x M 次 · 每次现造一个干净对象 · 收集事实 · 叫判据 · 排版报告 |
| 业务 | ① 怎么造一个被测对象 (`EvalSubject`) ② 怎么判 (`Judge`) |

两个接缝都在 `protocols.py`. 挂起 - 恢复、载荷注入这些业务特有的动作全留在业务
侧 —— 框架一点都不用知道 (issue 43 就是这么做的).

## 模块

| 模块 | 一句话 |
|------|--------|
| `protocols.py` | 两张协议: `EvalSubject` (造对象 + 跑一次) 与 `Judge` (判一次) |
| `runner.py` | 跑批器: 串行、每一跑现造对象、判据异常隔离、开跑前自检 |
| `report.py` | 汇总 + 排版: JSON (程序读) 与 Markdown (人看, 七块) |
| `compare.py` | 对照入口: 读两份 JSON, 出差异表 |
| `utils/` | 词汇 `EvalCase` / `RunFacts` / `RunOutcome` / `Metric` / `Judgment` |

## 跑一次要写的那几行

```python
runner = EvalRunner(judges={"召回率": RecallJudge(), "准确率": PrecisionJudge()})
report = await runner.run(
    [EvalGroup(name="全挂", build_subject=build_full, cases=cases),
     EvalGroup(name="裁剪", build_subject=build_pruned, cases=cases)],
    times=3,
)
Path("report.json").write_text(report.to_json(), encoding="utf-8")
Path("report.md").write_text(report.to_markdown(), encoding="utf-8")
```

## 命令行

    python -m CharAgent.eval compare 改前.json 改后.json

**不进根门面** (与 `client` / `server` 同): 跑分要真 API key 才能跑, 而根门面的
承诺是「装了这个包就能用」. 要用它写 `from CharAgent.eval import EvalRunner`.
"""

from __future__ import annotations

from CharAgent.eval.compare import compare_files, compare_reports, pick_group
from CharAgent.eval.protocols import EvalSubject, Judge, SubjectFactory
from CharAgent.eval.report import (
    DASH,
    OUTCOME_LABELS,
    REPORT_SCHEMA_VERSION,
    load_report,
    render_comparison,
    render_markdown,
    summarize_attempts,
)
from CharAgent.eval.runner import (
    DEFAULT_TIMES,
    MAX_GROUPS,
    Attempt,
    EvalGroup,
    EvalReport,
    EvalRunner,
    GroupResult,
)
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
    "DASH",
    "DEFAULT_TIMES",
    "MAX_GROUPS",
    "OUTCOME_LABELS",
    "REPORT_SCHEMA_VERSION",
    "RUN_OUTCOME_FOR_LOOP",
    "Attempt",
    "CallRecord",
    "EvalCase",
    "EvalConfigError",
    "EvalError",
    "EvalGroup",
    "EvalReport",
    "EvalRunner",
    "EvalStartupError",
    "EvalSubject",
    "GroupResult",
    "Judge",
    "Judgment",
    "Metric",
    "RunFacts",
    "RunOutcome",
    "SubjectFactory",
    "compare_files",
    "compare_reports",
    "load_report",
    "pick_group",
    "render_comparison",
    "render_markdown",
    "run_outcome_of",
    "summarize_attempts",
]
