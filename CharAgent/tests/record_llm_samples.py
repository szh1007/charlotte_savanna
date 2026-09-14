"""真实 API 录制脚本 (issue 09): 为 MockLLM 回放模式产出样本 (非 pytest 用例).

**为什么是脚本而不是用例**: 录制要打真实 DeepSeek 端点、消耗额度, 产出物
(`fixtures/llm/*.json`) 又随仓库提交 —— 它是一次性的「取样」动作, 不该混进
`pytest tests/` 的默认集合或 integration 集合 (每跑一次就多花一次钱).

跑法 (需根 .env 的 `DEEPSEEK_*` 配置)::

    cd CharAgent && python tests/record_llm_samples.py

录三份样本 (样本名即 `tests/fixtures/llm/{name}.json`):

| 样本 | 场景 | 用途 |
|------|------|------|
| `text_stop` | 单轮纯文本回答 (不开放工具) | 回放模式的最小样本 |
| `tool_path` | 两轮工具链路 (调工具 → 结果回填 → 答复) | 轨迹 / 事件快照 / 契约 |
| `tool_path_thinking` | 同上但开思考模式 | reasoning_content 的解析与回填 (#11) |

体检: 录完当场检查「是否真的录到了想录的路径」(工具轮确实调了工具 / 工具结果
确实回填 / 思考样本至少一轮带思维链), 不合格就打印问题并以退出码 1 结束 ——
样本要进仓库, 录歪了不如不录. **体检通过才落盘**: 录歪时仓库里那份好样本保持
原样 (先写盘再体检会把好的覆盖掉, 只剩一个退出码能说明问题). 思考样本
**不要求每轮都有** reasoning_content:
上游实测收尾轮常常没有 (与 issue 07 §9.A 记的偶发同源).

**场景定义在本模块顶层导出** (工具 / 提问 / loop 构造): 用例复用同一份定义,
回放时「本次请求」才可能与「录制时的请求」逐字相比 (`MockLLM.replay(...,
verify_requests=True)`) —— 契约回归靠的就是这个。

录制用的是**本地假订单查询工具** (返回固定文本, 不连库): 录的是**模型侧**的
真实响应原文, 工具结果只是让对话往前走的那一句台词, 固定才能回放.

import 本模块不会打网络 —— 只有直接运行 (`__main__`) 才走 `main()`.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
# 两处路径: tests/ (helpers / mock_llm / trace / doubles) 与仓库根 (CharAgent 包)
for _path in (_HERE.parent, _HERE.parents[2]):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from dotenv import load_dotenv  # noqa: E402  (路径先就位, import 只能排后面)
from mock_llm import RecordingChatModel  # noqa: E402
from trace_assertions import PINNED_SEED, PINNED_TEMPERATURE, trace_of  # noqa: E402

from CharAgent.agent import AgentLoop, LoopGuard  # noqa: E402
from CharAgent.model import chat_model_from_env  # noqa: E402
from CharAgent.tool import Tool, tool  # noqa: E402

load_dotenv(_HERE.parents[2] / ".env")

# 提问刻意写明「先查订单再回答」: 录制要的是工具路径样本, 让模型走这条路
USER_QUESTION = "我的订单 20260701123456 到哪了? 请先调用工具查询订单状态, 再回答。"

# 假订单 (固定台词: 工具结果进 wire 历史, 回放时要能逐字复现)
FAKE_ORDER_STATUS = "订单 20260701123456 已发货, 承运商顺丰, 运单号 SF1234567890。"


@tool
def query_order(order_no: str) -> str:
    """按订单号查询订单状态与物流信息. 用户询问订单到哪了 / 物流进度时使用."""
    return FAKE_ORDER_STATUS


@tool
def refund(order_no: str) -> str:
    """为指定订单提交退款申请. 用户明确要求退款时使用."""
    return f"订单 {order_no} 的退款申请已受理, 预计 1-3 个工作日原路退回。"


TOOLS: list[Tool] = [query_order, refund]


def make_loop(
    model: Any,
    *,
    tools: list[Tool] | None,
    thinking: bool = False,
    **loop_kwargs: Any,
) -> AgentLoop:
    """按录制场景构造 loop (用例复用同一份: loop 配置一致, 请求才可比对).

    采样参数走确定性的那一组 (temperature=0 + 固定 seed, #61) —— 录制时也该
    这么钉, 免得样本里出现「碰巧这次没调工具」的路径. 回放用例额外要的东西
    (event_sink / saver / thread_id) 经 loop_kwargs 透传, 于是「loop 长什么样」
    只有这一处定义, 录制与回放不会各写一份.
    """
    return AgentLoop(
        model=model,
        tools=tools,
        guard=LoopGuard(max_turns=6),
        temperature=PINNED_TEMPERATURE,
        seed=PINNED_SEED,
        thinking=thinking,
        **loop_kwargs,
    )


async def _record(
    name: str,
    *,
    prompt: str,
    tools: list[Tool] | None,
    thinking: bool,
    note: str,
) -> RecordingChatModel:
    """跑一次真实链路 (只录在内存里, **不落盘** —— 落盘在体检通过之后).

    顺序很重要: 先写盘再体检的话, 一旦录歪, 仓库里那份好样本已经被覆盖掉了,
    只剩一个退出码 1 能说明问题 (见 main 与 _save).
    """
    recorder = RecordingChatModel(
        chat_model_from_env(),
        name=name,
    )
    try:
        loop = make_loop(recorder, tools=tools, thinking=thinking)
        await loop.run([{"role": "user", "content": prompt}])
    finally:
        await recorder.aclose()
    return recorder


def _save(recorder: RecordingChatModel, *, thinking: bool, note: str) -> Path:
    """体检通过后落盘 (样本元信息在这里补齐)."""
    return recorder.dump(
        sampling={
            "temperature": PINNED_TEMPERATURE,
            "seed": PINNED_SEED,
            "thinking": thinking,
        },
        note=note,
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def _check_tool_path(recorder: RecordingChatModel) -> list[str]:
    """工具路径样本的体检 (录到了想录的东西吗).

    判据直接复用轨迹断言 (trace_assertions): 「工具结果有没有回填」这类规则
    只在一处定义, 免得录制脚本与用例各判一套、还判得不一样.
    """
    problems: list[str] = []
    trace = trace_of(recorder)
    if trace.turn_count != 2:
        problems.append(f"期望两轮 (调工具 + 最终答复), 实际 {trace.turn_count} 轮")
    if not trace.tool_calls:
        problems.append("第 1 轮没有工具调用 (模型没走工具路径)")
    if not trace.contents[-1]:
        problems.append("第 2 轮没有正文 (没录到最终答复)")
    try:
        trace.assert_tool_result_backfilled("query_order")
    except AssertionError as exc:
        problems.append(str(exc))
    return problems


def _check_thinking(recorder: RecordingChatModel) -> list[str]:
    """思考模式样本的体检: **至少一轮**带思维链, 否则样本失去防线意义.

    为什么不是「每轮都要」: 上游并非每轮都吐 reasoning_content (实测收尾轮
    常常没有, 与 issue 07 §9.A 记的偶发同源) —— 拿每轮都有当门槛会把合格的
    样本判死.
    """
    with_reasoning = [
        index
        for index, response in enumerate(recorder.responses, start=1)
        if response.reasoning
    ]
    if with_reasoning:
        print(f"    带思维链的轮次: {with_reasoning}")
        return []
    return ["没有任何一轮带 reasoning_content (上游这次没吐思维链): 重跑再录"]


def _use_utf8_stdout() -> None:
    """控制台按 UTF-8 打印 (errors=replace).

    Windows 默认控制台编码是 GBK, 而模型答案里什么都可能有 (实测一个 🚚 就让
    整段打印崩掉) —— 打印的是给人看的体检结果, 不该因为一个字符把录制收尾
    打断. 样本文件本身一直是 UTF-8, 不受这里影响.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main() -> int:
    """依次录三份样本, 打印体检结果 (有问题返回 1, 便于脚本化)."""
    _use_utf8_stdout()
    failures: list[str] = []
    plan = (
        (
            "text_stop",
            {
                "prompt": "用一句话说明什么是 idempotency key (幂等键)。",
                "tools": None,
                "thinking": False,
                "note": "单轮纯文本回答, 不开放工具: 回放模式的最小样本",
            },
            lambda recorder: [] if len(recorder.exchanges) == 1 else ["期望 1 轮"],
        ),
        (
            "tool_path",
            {
                "prompt": USER_QUESTION,
                "tools": TOOLS,
                "thinking": False,
                "note": "带工具的两轮链路 (调 query_order → 结果回填 → 最终答复)",
            },
            _check_tool_path,
        ),
        (
            "tool_path_thinking",
            {
                "prompt": USER_QUESTION,
                "tools": TOOLS,
                "thinking": True,
                "note": "同上但开思考模式: reasoning_content 解析与回填的样本",
            },
            _check_thinking,
        ),
    )
    for name, kwargs, check in plan:
        print(f"--- 录制 {name} ...")
        recorder = await _record(name, **kwargs)
        problems = check(recorder)
        if problems:
            status, path = "有问题 (不落盘)", "仓库里那份样本保持原样"
        else:
            status = "OK"
            path = _save(recorder, thinking=kwargs["thinking"], note=kwargs["note"])
        print(
            f"    {status}: {len(recorder.exchanges)} 轮 -> {path}\n"
            f"{_indent(trace_of(recorder).describe())}"
        )
        failures.extend(f"{name}: {problem}" for problem in problems)
    if failures:
        print("\n录制结果不合格 (样本未提交前请修好):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n三份样本录制完成, 体检通过。")
    return 0


def _indent(text: str, *, prefix: str = "    ") -> str:
    """轨迹摘要缩进 (打印给人看的部分)."""
    return "\n".join(prefix + line for line in text.splitlines())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
