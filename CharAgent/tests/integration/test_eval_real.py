"""真实 DeepSeek API 上的跑分冒烟 (marker: eval).

**为什么单开一个 marker 而不并进 `integration`**: 那批验的是**协议字段** (tool_calls
的结构 / usage / reasoning_content 的分离), 一跑一条请求; 这一批验的是**整条跑分
流水线** —— 造对象 N 次、问 N 次、判 N 次、排一份报告. 它更慢更贵, 想知道的也不是
一件事, 所以默认排除的名单里各占一格.

它守的是 scripted 用例的结构性盲区: `MockLLM` 是假大脑, 它**永远不会**给出脚本
以外的决策, 于是「换成真大脑之后, 跑批器那套装配还成立吗」在 unit 层测不出来 ——
真模型可能一次都不调工具、可能调错、可能把话说半截, 而报告得照样出得来.

**断言只压流水线, 不压分数**: 真模型这次选没选对工具是**被测对象**, 不是断言对象
(L4 的口径本来就是「跑多次看通过率」). 这里断言的是: 报告七块齐全、JSON 读得回来、
`compare` 跑得通、记录层能按 run_id 读回这一跑的轨迹.

默认排除 (pytest.ini 的 addopts), 运行:
    pytest -m eval
(需根 .env 配置 DEEPSEEK_* 密钥; 调真实端点, 会消耗额度)
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv

from CharAgent.agent import AgentLoop, LoopGuard, LoopOutcome, ToolCallFact
from CharAgent.db.state import TOOL_CALL_STATUS_FOR_OUTCOME
from CharAgent.eval import (
    CallRecord,
    EvalCase,
    EvalGroup,
    EvalRunner,
    Judgment,
    Metric,
    RunFacts,
    compare_files,
    run_outcome_of,
)
from CharAgent.model import ChatModel, chat_model_from_env
from CharAgent.tool import tool

# 根 .env 位于本文件向上三层: tests/integration -> tests -> CharAgent -> 仓库根
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

pytestmark = pytest.mark.eval

if not os.getenv("DEEPSEEK_API_KEY"):
    pytest.skip(
        "DEEPSEEK_API_KEY 未配置, 跳过真实 API 跑分测试", allow_module_level=True
    )


def _what_time() -> str:
    """报当前 UTC 时刻 (与电商无关的极小工具)."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


NOW_TOOL = tool(_what_time, name="what_time")

CASE = EvalCase(
    id="t1",
    question="请调用 what_time 工具查一下现在几点, 然后用一句话告诉我",
    expect_tools=("what_time",),
)


class RealSubject:
    """用**真模型**跑一道题的被测对象 (业务侧装配的样板, 与电商无关)."""

    def __init__(self, model: ChatModel) -> None:
        self._model = model

    async def run_once(self, case: EvalCase) -> RunFacts:
        """问一句, 把 loop 的结果抄成事实."""
        loop = AgentLoop(self._model, [NOW_TOOL], guard=LoopGuard(max_turns=4))
        result = await loop.run([{"role": "user", "content": case.question}])
        return RunFacts(
            tool_calls=tuple(
                call for turn in result.turns for call in _as_records(turn.calls)
            ),
            answer=result.content,
            outcome=run_outcome_of(result.outcome),
            error=""
            if result.outcome is LoopOutcome.FINISHED
            else result.outcome.value,
            turns=result.turn_count,
            tokens=result.total_tokens,
            elapsed_ms=result.elapsed_ms,
            run_id=case.id,
            config={"模型": "deepseek (来自 .env)", "工具数": 1},
        )

    async def aclose(self) -> None:
        """关掉模型客户端 (每一跑各自持有, 不关会攒下 N 个连接池)."""
        await self._model.aclose()


def _as_records(calls: Sequence[ToolCallFact]) -> list[CallRecord]:
    """`ToolCallFact` → `CallRecord` (状态口径走框架那张映射表, 不另造一套)."""
    return [
        CallRecord(
            tool_name=call.tool_name,
            arguments=call.arguments,
            status=TOOL_CALL_STATUS_FOR_OUTCOME[call.outcome],
            duration_ms=call.duration_ms,
        )
        for call in calls
    ]


class RecallJudge:
    """期望的工具调到了吗 (召回率那一路, 分子分母都交出去)."""

    def judge(self, case: EvalCase, facts: RunFacts) -> Judgment:
        """判一次."""
        called = set(facts.tool_names)
        missed = [name for name in case.expect_tools if name not in called]
        return Judgment(
            ok=not missed,
            reason="" if not missed else f"没调 {missed}, 实际调了 {facts.tool_names}",
            metrics={
                "召回率": Metric(
                    len(case.expect_tools) - len(missed), len(case.expect_tools)
                )
            },
        )


async def _build(case: EvalCase) -> RealSubject:
    """每一跑现造一个对象 (每一跑各自一个模型客户端)."""
    return RealSubject(chat_model_from_env())


async def test_a_real_run_produces_a_report_and_a_comparison(tmp_path: Path) -> None:
    """真模型跑一遍: 七块齐全, JSON 读得回来, 两份报告能对照."""
    runner = EvalRunner(judges={"召回率": RecallJudge()})
    group = EvalGroup(name="真跑", build_subject=_build, cases=[CASE])
    report = await runner.run([group], times=1)

    attempts = report.group("真跑").attempts
    assert len(attempts) == 1
    # 真模型这次跑成什么样是**被测对象**, 这里只要求它有个明确的终局
    assert attempts[0].facts.outcome.value in {
        "completed",
        "truncated",
        "suspended",
        "broken",
    }

    text = report.to_markdown()
    for heading in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7."):
        assert heading in text

    path = tmp_path / "真跑.json"
    path.write_text(report.to_json(), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["times"] == 1
    assert compare_files(str(path), str(path)).startswith("# 对照:")
