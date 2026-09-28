"""跑分入口: 两组一起跑, 落一份 JSON + 一份 Markdown (issue 44 的工具数量 A/B).

一句话理解: 把三片零件拼成一次**实验** —— 题集 (issue 42) x 跑分环境 (issue 41) x
模拟确认 (issue 43), 两组各跑一遍, 报告落在盘上. 两组只差**一个开关**: 全挂组的可见
集是全部工具, 裁剪组的可见集由分类器按题面给 (`minimall/scoping.py`). 这一条是 A/B
成立的前提, 而它在报告头部那块配置快照里看得见 (`工具范围` 那一行).

**为什么还要一个业务侧入口** (框架不是有跑批器吗): 跑批器管流程, 而它不知道「这批
题从哪读、两组差在什么上、报告落到哪」—— 那三件都是业务的. 框架的 `python -m
CharAgent.eval` 只做**两份报告之间的对照** (读文件, 不跑模型).

**第八块: 分类错误** (票据第五节要的那一行): 裁剪组每道题开哪几组由**规则分类器**
给, 而分错组的后果是**期望工具压根没给模型** —— 那一跑注定失败, 而它不是模型选错
了, 是装置坏了. 这块把逐题的分类结果、以及「只数分类正确的题」的分数单列出来.

它不是第七块那种「框架算好的数」: 框架不认识分类器, 也就不该替它算. 于是它**追加
在 Markdown 尾巴上** (人读的那份), 与框架那七块之间有一条分隔线 —— 框架那份 JSON
是 `compare` 要读的规范形状, 往里塞业务键会让两边分家.

用法::

    python -m CharApp.eval.run                       # 20 题 x 3 次 x 2 组
    python -m CharApp.eval.run --times 1 --cases product-01,order-03
    python -m CharApp.eval.run --out .scratch/44-tools

跑一次要真模型 (几十到上百次问答), 所以**它不在 pytest 里** —— 用例跑的是不触网的
那一层 (判据 / 裁剪 / 报告那几块).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from CharAgent.client import CliOptions, load_root_env, use_utf8_stdio
from CharAgent.eval import (
    DASH,
    DEFAULT_TIMES,
    EvalCase,
    EvalGroup,
    EvalReport,
    EvalRunner,
    EvalSubject,
    SubjectFactory,
    summarize_attempts,
)
from CharAgent.model.protocol import ChatModel
from CharApp.eval.golden import load_cases
from CharApp.eval.judges import DEFAULT_JUDGES, PRECISION, RECALL
from CharApp.eval.subject import subject_factory
from CharApp.minimall.log_redaction import build_redactor, redacting_writer
from CharApp.minimall.scoping import (
    SCOPE_FULL,
    SCOPE_PRUNED,
    classify,
    tools_of,
)
from CharApp.minimall.service import STARTUP_ERRORS, build_model_for

# 报告默认落到哪 (前缀; 三种文件按它加后缀). 放仓库里是因为它是 L4 的产出物 ——
# issue 46 要把这两组数据写进文档 (那句「L4 的 A/B 数据要留好」)
DEFAULT_OUT = Path(__file__).resolve().parent / "reports" / "44-tools-ab"

# 输出出口的形状 (进度 / 重试提示 / 结果路径都从它走)
Writer = Callable[[str], Any]

# 第八块要摆的两个工具指标 (名字来自判据那一页的常量, 不是另抄一份字面量):
# 分类器分错组只可能打中「期望的那个工具没给」, 所以看的正是召回率与准确率.
METRICS: tuple[str, ...] = (RECALL, PRECISION)


def build_parser() -> argparse.ArgumentParser:
    """命令行参数 (帮助文本是对外契约, 与 `minimall/cli.py` 同一个做法)."""
    parser = argparse.ArgumentParser(
        prog="python -m CharApp.eval.run",
        description="跑一次工具数量 A/B (全挂 vs 按题裁剪), 落 JSON + Markdown",
    )
    parser.add_argument(
        "--times",
        type=int,
        default=DEFAULT_TIMES,
        help=f"每题跑几次 (波动那一块按它切片), 默认 {DEFAULT_TIMES}",
    )
    parser.add_argument(
        "--cases",
        default="",
        help="只跑这几道 (题号, 逗号分隔; 不写 = 全部) —— 冒烟与复现单题用",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"报告的路径前缀 (会写成 <前缀>.json 与 <前缀>.md), 默认 {DEFAULT_OUT}",
    )
    parser.add_argument(
        "--model",
        metavar="NAME",
        help="模型名覆盖 (默认听 .env 的 DEEPSEEK_MODEL_NAME); 两组用的是同一个",
    )
    return parser


def pick_cases(cases: Sequence[EvalCase], wanted: str) -> tuple[EvalCase, ...]:
    """按 `--cases` 挑题 (不写就全要, 顺序照题集).

    Raises:
        ValueError: 题号不在题集里 —— 静默少跑几道题会让一份报告看起来「完整」,
            而它其实少了题 (与题集校验那条纪律同源: 写错的名字当场说清).
    """
    names = [name.strip() for name in wanted.split(",") if name.strip()]
    if not names:
        return tuple(cases)
    known = {case.id: case for case in cases}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ValueError(
            f"题集里没有 {unknown} 这些题号 (有的是: {', '.join(sorted(known))})"
        )
    return tuple(known[name] for name in names)


def with_progress(
    factory: SubjectFactory, *, label: str, total: int, writer: Writer
) -> SubjectFactory:
    """给工厂包一层进度输出 (真模型那一趟要跑很久, 没进度像是卡住了).

    包在**工厂**这一层而不是跑批器里: 框架不知道一次实验要跑多久, 而这里是业务自己
    的入口. 输出走 `redacting_writer` —— 跑分这条链上任何输出都必须过打码
    (见 `subject.py` 模块 docstring 最后一段).

    **第一次调用不算一跑**: 跑批器开跑前会为每组造一个对象自检 (`runner._probe`,
    造完就关、不跑), 于是每一次真跑都排在它后面. 不扣掉它的话计数器会走到
    「3/2」这种看着像多跑了一下的数 (与 `subject_factory` 那边「记录里第一条是
    `-2` 起」是同一件事的两面).
    """
    done = -1

    async def build(case: EvalCase) -> EvalSubject:
        """造一个对象, 顺带报一句「跑到哪了」(自检那一次不报)."""
        nonlocal done
        done += 1
        if done > 0:
            writer(f"[{label}] {done}/{total} {case.id}")
        return await factory(case)

    return build


def build_groups(
    cases: Sequence[EvalCase],
    *,
    times: int,
    writer: Writer,
    model_name: str | None = None,
    model_for: Callable[[EvalCase], ChatModel] | None = None,
) -> tuple[EvalGroup, EvalGroup]:
    """两组: 只差 `prune_tools` 这一个开关 (其余旋钮同源, 模型也是同一个名字).

    「两组只差一个开关」是这次 A/B 成立的前提, 所以两组的工厂由**同一处**造 ——
    谁想给其中一组多配一个旋钮, 都得先在这几行里写明.

    Args:
        model_name: 模型名覆盖; None 表示听 .env (只在 `model_for` 也给 None 时看它).
        model_for: 造模型的缝 —— 与全仓其他测试同一套做法 (塞个 MockLLM 进去就能
            离线跑通整条链路, 被测代码一行不改); None = 按 `model_name` 造真的.
    """
    if model_for is None:
        options = CliOptions(model_name=model_name, use_retry=True)

        def build_one(_case: EvalCase) -> ChatModel:
            """这一跑的模型 (每一跑现造一个: 所有权随装配交出去, 见 `subject_factory`).

            题面用不上 —— 两组用的是同一个模型名, 差异只在裁剪那一个开关上. 但**签名
            里必须收下它**: 工厂按 `(case) -> 模型` 调用 (见 `protocols.py`).
            """
            return build_model_for(options, writer)

        model_for = build_one

    total = len(cases) * times
    return (
        EvalGroup(
            name=SCOPE_FULL,
            build_subject=with_progress(
                subject_factory(model_for),
                label=SCOPE_FULL,
                total=total,
                writer=writer,
            ),
            cases=tuple(cases),
        ),
        EvalGroup(
            name=SCOPE_PRUNED,
            build_subject=with_progress(
                subject_factory(model_for, prune_tools=True),
                label=SCOPE_PRUNED,
                total=total,
                writer=writer,
            ),
            cases=tuple(cases),
        ),
    )


async def run_ab(
    cases: Sequence[EvalCase],
    *,
    times: int,
    model_name: str | None = None,
    model_for: Callable[[EvalCase], ChatModel] | None = None,
    writer: Writer = print,
) -> EvalReport:
    """两组各跑一遍 (同一批题、同一个模型、同一套判据), 交回报告.

    Args:
        cases: 这一批的题 (两组用的是**同一份**).
        times: 每题跑几次.
        model_name: 模型名覆盖; None 表示听 .env.
        model_for: 造模型的缝 (见 `build_groups`); None = 造真的.
        writer: 进度与重试提示的出口 (走打码那一层).

    Returns:
        EvalReport: 两个组的全部跑次与判据结论.
    """
    groups = build_groups(
        cases, times=times, writer=writer, model_name=model_name, model_for=model_for
    )
    return await EvalRunner(judges=DEFAULT_JUDGES).run(groups, times=times)


def write_report(report: EvalReport, prefix: Path) -> tuple[Path, Path, Path]:
    """报告落盘: 框架的 JSON + Markdown, 外加第八块的 sidecar JSON.

    三份文件各有各的读者:

    - `<前缀>.json` —— 框架的规范形状 (issue 40 定的), `python -m CharAgent.eval
      compare` 读它. **一个业务键都不往里塞** (塞了就是两份口径混在一份文件里).
    - `<前缀>.md` —— 人看的: 框架那七块 + **业务追加的第八块** (分类错误). 它必须与
      七块在同一份文件里, 否则读报告的人会把装置的锅算到模型头上.
    - `<前缀>-scoping.json` —— 第八块的机器可读版 (票据交付物 6 要的「JSON 含分类
      那一行」). 单开一份而不是塞进上面那份, 理由见第一条: 框架那份是**两份报告
      之间可比**的形状, 多一个键就多一处漂移.

    Returns:
        tuple[Path, Path, Path]: (框架 JSON, Markdown, 第八块 JSON).
    """
    prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = Path(f"{prefix}.json")
    markdown_path = Path(f"{prefix}.md")
    scoping_path = Path(f"{prefix}-scoping.json")
    json_path.write_text(report.to_json(), encoding="utf-8")
    markdown_path.write_text(
        report.to_markdown() + "\n" + scoping_section(report), encoding="utf-8"
    )
    scoping_path.write_text(
        json.dumps(scoping_data(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return json_path, markdown_path, scoping_path


# ---------------------------------------------------------------------------
# 第八块: 分类错误 (装置那一侧, 不归模型)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopingRow:
    """一道题的分类结果 (第八块那张表的一行).

    attributes:
        case_id: 题号.
        groups: 分类器给这道题开的组.
        visible: 可见集 (那几组盖住的工具名).
        expect: 题面期望的工具集.
    """

    case_id: str
    groups: tuple[str, ...]
    visible: frozenset[str]
    expect: tuple[str, ...]

    @classmethod
    def of(cls, case: EvalCase) -> ScopingRow:
        """按题面算出一行 (分类器是无状态纯函数, 随时算得出来)."""
        groups = classify(case.question)
        return cls(
            case_id=case.id,
            groups=groups,
            visible=tools_of(groups),
            expect=tuple(case.expect_tools),
        )

    @property
    def missing(self) -> tuple[str, ...]:
        """期望了却没给模型的那些工具 (非空 = 分类错误)."""
        return tuple(sorted(set(self.expect) - self.visible))

    @property
    def correct(self) -> bool:
        """分类对不对 —— 判据就是票据那句「期望工具集 ⊄ 可见集 即错」."""
        return not self.missing

    @property
    def verdict(self) -> str:
        """那一格: 对了打勾, 错了写清少了什么."""
        if self.correct:
            return "✅"
        return f"❌ 少了 {' / '.join(self.missing)}"


def scoping_data(report: EvalReport) -> dict[str, Any]:
    """整份报告 → 第八块的规范数据 (Markdown 与 sidecar JSON 同源).

    两块内容, 各自回答一个问题:

    - **逐题表** (`cases`): 分类器给每道题开了哪几组、期望工具是不是真在里面
      (❌ 那几行就是装置坏了的那几条).
    - **分数** (`correct` / `wrong` / `scores`): 「只数分类正确的题」的召回率 /
      准确率, 连同该组整体的那两个数 —— 两者不等时, 差额就是装置的那部分, 不该
      算进 A/B 的结论.

    与框架那两份文件同一个做法 (算一次、两处看): Markdown 是这份数据的排版.
    """
    rows = [ScopingRow.of(case) for case in _cases_of(report)]
    wrong = [row for row in rows if not row.correct]
    correct_ids = {row.case_id for row in rows if row.correct}
    return {
        "cases": [
            {
                "case_id": row.case_id,
                "groups": list(row.groups),
                "visible_tools": sorted(row.visible),
                "expect_tools": list(row.expect),
                "missing": list(row.missing),
                # 那一格是给人看的 (Markdown 直接用), 但机器读的那份也别落下 ——
                # `missing` 是同一件事的机器可读版, 两者都在
                "verdict": row.verdict,
            }
            for row in rows
        ],
        "correct": len(rows) - len(wrong),
        "wrong": len(wrong),
        "doomed_attempts": _doomed_attempts(report, wrong),
        "scores": _scores(report, correct_ids),
    }


def scoping_section(report: EvalReport) -> str:
    """第八块 → Markdown (人是这么读的; 数据在 `scoping_data`)."""
    data = scoping_data(report)
    lines = [
        "",
        "---",
        "",
        "## 8. 分类错误 (装置那一侧, 不归模型)",
        "",
        "裁剪组每道题开哪几组由**规则分类器**按题面给 (`scoping.classify`). 分错组的",
        "后果是**期望工具压根没给模型** —— 工具选择那一栏因此必不过 (其余判据不一定:",
        "那一跑照样可能答得对). 这一块把那几行单列出来, 并给一份**只数分类正确的题**",
        "的分数.",
        "",
        "> 读第 1 块时注意: 「工具数」是**装配态** (两臂都是同一批工具, 裁剪发生在轮次",
        "> 里), 每一跑真给模型看的是下面「可见工具数」那一列.",
        "",
        "| 题号 | 分类器给的组 | 可见工具数 | 期望工具 | 结论 |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {item['case_id']} | {' / '.join(item['groups'])} "
        f"| {len(item['visible_tools'])} "
        f"| {' / '.join(item['expect_tools']) or DASH} "
        f"| {item['verdict']} |"
        for item in data["cases"]
    ]
    lines += [
        "",
        f"- 分类正确: {data['correct'] + data['wrong']} 道里对了 {data['correct']} 道"
        f" (错 {data['wrong']} 道)",
        "- 只数分类正确的题 (这才是 A/B 该比的那两个数; 括号里是该组整体):",
    ]
    lines += [
        f"  - {name}: " + " · ".join(_metric_text(item, metric) for metric in METRICS)
        for name, item in data["scores"].items()
    ]
    lines += [
        f"- 因分类错误而**工具选择必然不过**的跑次: {data['doomed_attempts']}"
        f" (那些题上的分数不计入结论)",
        "",
    ]
    return "\n".join(lines)


def _scores(report: EvalReport, correct_ids: set[str]) -> dict[str, Any]:
    """每个组「只数分类正确的题」的那两个数 (连该组整体一起交出来, 让差额看得见)."""
    scores: dict[str, Any] = {}
    for group in report.groups:
        subset = summarize_attempts(
            [item for item in group.attempts if item.case.id in correct_ids],
            report.judges,
        )
        overall = summarize_attempts(group.attempts, report.judges)
        scores[group.name] = {
            **{metric: _rate_of(subset, metric) for metric in METRICS},
            "overall": {metric: _rate_of(overall, metric) for metric in METRICS},
        }
    return scores


def _rate_of(summary: Mapping[str, Any], metric: str) -> float | None:
    """汇总里「工具选择」那一条的某个子指标 → 比率; 没有分母给 None.

    读的是框架那份汇总的**规范形状** (判据名 → 子指标 → `rate`). 它比 `_render`
    那种显示层的口径稳 —— 显示层的排版是框架的私事, 而这一格只拿数.
    """
    choice = (summary.get("judges") or {}).get("工具选择") or {}
    item = (choice.get("metrics") or {}).get(metric) or {}
    return item.get("rate")


def _percent(rate: float | None) -> str:
    """比率 → 百分数文本 (**与报告那几块同一个排版**: 一位小数).

    框架的 `_render` 是私有的, 这里只能照它的口径写一遍 —— 好在这一格与第 2 块并排
    摆着, 排版漂了当场就看得出来 (同一份文件里两个 88.9% 不该长得不一样).
    """
    return DASH if rate is None else f"{rate * 100:.1f}%"


def _metric_text(item: Mapping[str, Any], metric: str) -> str:
    """一个组的那一格: 「子指标 本组比率 (整体 比率)」."""
    return (
        f"{metric} {_percent(item.get(metric))} "
        f"(整体 {_percent((item.get('overall') or {}).get(metric))})"
    )


def _doomed_attempts(report: EvalReport, wrong: Sequence[ScopingRow]) -> int:
    """分错组的那几道题一共跑了几次.

    数的是**跑次**而不是题: 每一跑都是一次要花钱的问答, 而它们全都带着一个已知的
    装置缺陷. 判据彼此独立, 所以「必然不过」只对**工具选择**那一条成立 —— 那些跑次
    照样可能把话说对 (答复 / 合规可能过). 说清这一点, 是为了别把这一行读成
    「这些跑次全是废的」.
    """
    ids = {row.case_id for row in wrong}
    return sum(
        1 for group in report.groups for item in group.attempts if item.case.id in ids
    )


def _cases_of(report: EvalReport) -> tuple[EvalCase, ...]:
    """报告里出现过的题 (按首次出现的顺序去重).

    两组跑的是同一批题, 但这份报告不假定这一点 (跑批器不强制) —— 照实际用过的题算,
    于是「某组少跑了一道」这种事在这一块里也是看得见的.
    """
    seen: dict[str, EvalCase] = {}
    for group in report.groups:
        for case in group.cases:
            seen.setdefault(case.id, case)
    return tuple(seen.values())


def main(argv: Sequence[str] | None = None) -> int:
    """命令入口: 读环境 → 挑题 → 跑两组 → 落两份文件 → 报路径.

    Returns:
        int: 进程退出码 (0 正常; 1 参数或配置错; 130 被 Ctrl-C 打断).
    """
    load_root_env()
    use_utf8_stdio()
    args = build_parser().parse_args(argv)
    # 跑分这条链上的输出与重试提示都过打码 (威胁模型是「留痕去哪」, 见 subject.py)
    writer = redacting_writer(print, build_redactor())
    try:
        cases = pick_cases(load_cases(), args.cases)
        report = asyncio.run(
            run_ab(cases, times=args.times, model_name=args.model, writer=writer)
        )
    except STARTUP_ERRORS as exc:
        # 配置类错误 (缺令牌 / 缺 key / 提示词不在): 报一句人话, 不打印 traceback
        writer(f"启动失败: {type(exc).__name__}: {exc}")
        return 1
    except ValueError as exc:
        writer(f"参数不对: {exc}")
        return 1
    except KeyboardInterrupt:
        writer("")
        writer("已打断 (报告没落盘: 这一批跑次不完整)")
        return 130
    # 落盘在 try **外面**: 走到这里这一批已经跑完了, 而「报告写不进去」是磁盘的事
    # (让它带着 traceback 炸出来, 别混进「启动失败」那句人话里)
    paths = write_report(report, Path(args.out))
    writer("报告落盘: " + " / ".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
