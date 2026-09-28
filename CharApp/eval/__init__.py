"""eval 包: 业务侧的离线跑分 (L4).

一句话理解: 拿一批题去问**真模型**、记下它调了什么、按规则判对错、出一份能对比
两次跑分的报告. 框架侧给协议与跑批器 (`CharAgent/eval/`), 业务侧给三样 —— 题与
判据 (issue 42) · 跑分环境 (issue 41) · 两个 A/B 的实验装置 (issue 44 / 45).

| 文件 | 管什么 |
|------|--------|
| `fixtures.py` | 跑分用的样本与假商城 (`tests/conftest.py` 搬来的那份) + 敏感值清单 |
| `harness.py` | 跑分环境: 假商城 + 内存快照 + 假记录库 + 装好的服务 (问答一套零件) |
| `subject.py` | 被测对象: 问一句, 停在确认点上就**替买家点一下**, 交回一份事实 |
| `golden.py` | 题集: `cases/*.yaml` 的加载与校验 |
| `judges.py` | 判据: 三个工具指标 + 答复 + 回答合规 + 护栏 |
| `cases/` | 题本身 (六个 YAML, 一页一个场景) |

**它不是 pytest 用例集**: 跑分要落报告、要比两份文件、要传参数, 那是**独立入口**
的活 (入口与题集在 issue 42 之后那几片); `pytest` 里只留框架侧的自证用例
(issue 40) 与不触网的判据用例 (issue 42 起).

**打的是真模型、跑的是假商城** (L4 规划期定的): 前者是非确定性要跑多次的那个
变量, 后者让「跑分」不依赖 Django 也不依赖真库. 判据**只走规则**, 不引 LLM-judge.
"""

from __future__ import annotations

from CharApp.eval.fixtures import (
    AGENT_BASE_URL,
    BIG_CART_BUYER,
    BUYER_ID,
    SENSITIVE_VALUES,
    TOKEN,
    agent_url,
    build_mall,
    mock_all,
)
from CharApp.eval.golden import SCENES, load_cases
from CharApp.eval.harness import EvalHarness, open_harness
from CharApp.eval.judges import (
    DEFAULT_JUDGES,
    PARAMS,
    PRECISION,
    RECALL,
    AnswerJudge,
    ArgsJudge,
    ComplianceJudge,
    GuardrailJudge,
    ToolChoiceJudge,
)
from CharApp.eval.subject import HarnessSubject, subject_factory

__all__ = [
    "AGENT_BASE_URL",
    "BIG_CART_BUYER",
    "BUYER_ID",
    "DEFAULT_JUDGES",
    "PARAMS",
    "PRECISION",
    "RECALL",
    "SCENES",
    "SENSITIVE_VALUES",
    "TOKEN",
    "AnswerJudge",
    "ArgsJudge",
    "ComplianceJudge",
    "EvalHarness",
    "GuardrailJudge",
    "HarnessSubject",
    "ToolChoiceJudge",
    "agent_url",
    "build_mall",
    "load_cases",
    "mock_all",
    "open_harness",
    "subject_factory",
]
