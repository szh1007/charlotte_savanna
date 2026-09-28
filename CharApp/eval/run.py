"""跑分入口: 两组一起跑, 落一份 JSON + 一份 Markdown (L4 那两次 A/B).

一句话理解: 把三片零件拼成一次**实验** —— 题集 (issue 42) x 跑分环境 (issue 41) x
模拟确认 (issue 43), 两组各跑一遍, 报告落在盘上. 两组只差**一个开关**, 而拨的是哪
一个由实验决定 (`EXPERIMENTS` 那张表):

| 实验 | 自变量 | 两臂 | 报告 |
|------|--------|------|------|
| `tools` (issue 44) | 按题面裁不裁工具 | 全挂 / 按题裁剪 | 三份 (多一块分类错误) |
| `prompt` (issue 45) | 提示词版本 | v3 / v4 | 两份 (主指标在框架那七块里) |

「两组只差一个开关」是 A/B 成立的前提, 而它在报告头部那块配置快照里看得见
(`工具范围` / `提示词` 那两行).

**为什么还要一个业务侧入口** (框架不是有跑批器吗): 跑批器管流程, 而它不知道「这批
题从哪读、两组差在什么上、报告落到哪」—— 那三件都是业务的. 框架的 `python -m
CharAgent.eval` 只做**两份报告之间的对照** (读文件, 不跑模型).

**第八块: 分类错误** (issue 44 票据要的那一行): 裁剪组每道题开哪几组由**规则分类器**
给, 而分错组的后果是**期望工具压根没给模型** —— 那一跑注定失败, 而它不是模型选错
了, 是装置坏了. 这块把逐题的分类结果、以及「只数分类正确的题」的分数单列出来.

它不是第七块那种「框架算好的数」: 框架不认识分类器, 也就不该替它算. 于是它**追加
在 Markdown 尾巴上** (人读的那份), 与框架那七块之间有一条分隔线 —— 框架那份 JSON
是 `compare` 要读的规范形状, 往里塞业务键会让两边分家.

**prompt 那次没有第八块**: 它没有分类器, 主指标 (回答合规 / markdown 强调) 在框架
那七块里就有 —— 于是它只落两份 (见 `EXPERIMENTS` 里那两行各自挂的写法).

用法::

    python -m CharApp.eval.run                          # 工具 A/B: 20 题 x 3 次 x 2 臂
    python -m CharApp.eval.run --experiment prompt      # prompt A/B (v3 vs v4)
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
from CharApp.minimall.service import (
    STARTUP_ERRORS,
    build_model_for,
    resolve_prompt_version,
)

# 报告落到哪个目录 (文件名由 `Experiment.out` 给). 放仓库里是因为它们是 L4 的产出物
# —— issue 46 要把这几次的数据写进文档 (那句「L4 的 A/B 数据要留好」)
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# 输出出口的形状 (进度 / 重试提示 / 结果路径都从它走)
Writer = Callable[[str], Any]

# 不带 `--experiment` 时跑哪一次 (issue 44 那次是入口建起来时先有的, 于是它是默认)
DEFAULT_EXPERIMENT = "tools"

# prompt A/B 比的那两版 (issue 45). 版本号写在这里, **不从清单读**: 清单那一行的
# 语义是「生产用哪一版」, 而对照组必须是**指名的**那一版 —— 靠清单的话, 谁把默认换成
# v4 的那一刻, 两组就都成了 v4, 而报告上只是一次「两版成绩差不多」.
PROMPT_BASELINE = "v3"
PROMPT_VARIANT = "v4"


@dataclass(frozen=True, slots=True)
class Arm:
    """一次 A/B 里的一臂: 名字 + 它被拨动的那个开关.

    两个开关都在这里, 而**一次实验只拨一个** —— 两臂由同一处造出来 (见
    `build_groups`), 谁想给其中一臂多配一个旋钮, 都得先在这个类型上加一个字段;
    而那一眼就看得见 (它会让"两组只差一个开关"这句话当场变成假话).

    attributes:
        name: 组名 (报告里那一列的表头, 也是进度行方括号里的字).
        prune_tools: 这一臂要不要按题面裁工具 (issue 44 的自变量).
        prompt_version: 这一臂用哪一版提示词 (issue 45 的自变量); None = 读清单.
    """

    name: str
    prune_tools: bool = False
    prompt_version: str | None = None


# 工具数量 A/B 的两臂: 只差"按题面裁不裁" (issue 44)
TOOLS_ARMS: tuple[Arm, Arm] = (
    Arm(name=SCOPE_FULL),
    Arm(name=SCOPE_PRUNED, prune_tools=True),
)

# prompt A/B 的两臂: 只差"哪一版提示词" (issue 45). 组名就用版本号 —— 报告的组表头
# 因此直接写着 `v3` / `v4`, 与配置快照那一格里 `system/v3` 对得上
PROMPT_ARMS: tuple[Arm, Arm] = (
    Arm(name=PROMPT_BASELINE, prompt_version=PROMPT_BASELINE),
    Arm(name=PROMPT_VARIANT, prompt_version=PROMPT_VARIANT),
)


@dataclass(frozen=True, slots=True)
class Experiment:
    """一次 A/B 的整份定义: 两臂 + 报告落哪儿 + 人读那份要不要追加一块.

    attributes:
        arms: 两臂 (第一个是**对照组**).
        out: 报告的路径前缀 (三种文件按它加后缀).
        write: 落盘那一手 —— 两次实验落几份文件不同, 差异全在这一个字段里 (见
            `write_report` 与 `write_tools_report`), 而"两份报告都从 `to_dict()`
            渲染"那条纪律不变.
    """

    arms: tuple[Arm, Arm]
    out: Path
    write: Callable[[EvalReport, Path], tuple[Path, ...]]


# 第八块要摆的两个工具指标 (名字来自判据那一页的常量, 不是另抄一份字面量):
# 分类器分错组只可能打中「期望的那个工具没给」, 所以看的正是召回率与准确率.
METRICS: tuple[str, ...] = (RECALL, PRECISION)


def build_parser() -> argparse.ArgumentParser:
    """命令行参数 (帮助文本是对外契约, 与 `minimall/cli.py` 同一个做法)."""
    parser = argparse.ArgumentParser(
        prog="python -m CharApp.eval.run",
        description="跑一次 A/B (工具数量 / 提示词版本), 落 JSON + Markdown",
    )
    parser.add_argument(
        "--experiment",
        choices=sorted(EXPERIMENTS),
        default=DEFAULT_EXPERIMENT,
        help=(
            "跑哪一次 A/B: tools = 工具数量 (issue 44), prompt = 提示词版本 (issue 45)"
            f" —— 默认 {DEFAULT_EXPERIMENT}"
        ),
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
        default=None,
        help="报告的路径前缀 (默认落到该次实验的 reports/<它>), 冒烟时不落仓库用",
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
    arms: tuple[Arm, Arm],
    model_name: str | None = None,
    model_for: Callable[[EvalCase], ChatModel] | None = None,
) -> tuple[EvalGroup, ...]:
    """两臂 → 两个组: **同一处**造, 于是差别只可能来自 `Arm` 里那几个字段.

    「两组只差一个开关」是 A/B 成立的前提, 所以两个工厂由这里一次生成 —— 谁想给
    其中一组多配一个旋钮, 都得先写进 `Arm`.

    Args:
        cases: 这一批的题 (两臂用的是**同一份**).
        times: 每题跑几次 (进度按 `题数 x 次数` 报).
        writer: 进度与重试提示的出口.
        arms: 两臂 (顺序 = 报告的列序; 第一个是对照组).
        model_name: 模型名覆盖; None 表示听 .env (只在 `model_for` 也给 None 时看它).
        model_for: 造模型的缝 —— 与全仓其他测试同一套做法 (塞个 MockLLM 进去就能
            离线跑通整条链路, 被测代码一行不改); None = 按 `model_name` 造真的.
    """
    if model_for is None:
        options = CliOptions(model_name=model_name, use_retry=True)

        def build_one(_case: EvalCase) -> ChatModel:
            """这一跑的模型 (每一跑现造一个: 所有权随装配交出去, 见 `subject_factory`).

            题面用不上 —— 两臂用的是同一个模型名, 差异只在各自的开关上. 但**签名
            里必须收下它**: 工厂按 `(case) -> 模型` 调用 (见 `protocols.py`).
            """
            return build_model_for(options, writer)

        model_for = build_one

    check_versions(arms)
    total = len(cases) * times
    return tuple(
        EvalGroup(
            name=arm.name,
            build_subject=with_progress(
                subject_factory(
                    model_for,
                    prune_tools=arm.prune_tools,
                    prompt_version=arm.prompt_version,
                ),
                label=arm.name,
                total=total,
                writer=writer,
            ),
            cases=tuple(cases),
        )
        for arm in arms
    )


def check_versions(arms: Sequence[Arm]) -> None:
    """两臂钉的提示词版本都在盘上吗 —— 开跑**之前**一次说清.

    为什么不能靠跑批器那条自检: 那条验的是「第一个被测对象装得出来吗」
    (`runner._probe`), 而提示词版本是**跑到第一题**才读的 (`run_once` → `open_harness`
    → `session_for`) —— 少了这一句, 一个写错的版本号会让 120 跑一起记成 `BROKEN`
    (每一跑的 `error` 里都写着同一句真因), 而入口那句「报告落盘」照样报成功.

    版本不在盘上属于"环境没配好", 与「缺 API key」同一类: 该在开跑前一句话说清
    (跑批器那边为同一件事专门留了 `EvalStartupError` 那一档).

    Raises:
        MinimallConfigError: 某一臂钉的版本在盘上没有对应的 `.prompt` (启动期错误).
    """
    for arm in arms:
        if arm.prompt_version is not None:
            resolve_prompt_version(version=arm.prompt_version)


async def run_ab(
    cases: Sequence[EvalCase],
    *,
    times: int,
    arms: tuple[Arm, Arm],
    model_name: str | None = None,
    model_for: Callable[[EvalCase], ChatModel] | None = None,
    writer: Writer = print,
) -> EvalReport:
    """两臂各跑一遍 (同一批题、同一个模型、同一套判据), 交回报告.

    Args:
        cases: 这一批的题 (两臂用的是**同一份**).
        times: 每题跑几次.
        arms: 两臂 (见 `build_groups`).
        model_name: 模型名覆盖; None 表示听 .env.
        model_for: 造模型的缝 (见 `build_groups`); None = 造真的.
        writer: 进度与重试提示的出口 (走打码那一层).

    Returns:
        EvalReport: 两个组的全部跑次与判据结论.

    Raises:
        MinimallConfigError: 某一臂钉的提示词版本不在盘上 (开跑前拦下, 见
            `check_versions`).
    """
    groups = build_groups(
        cases,
        times=times,
        writer=writer,
        arms=arms,
        model_name=model_name,
        model_for=model_for,
    )
    return await EvalRunner(judges=DEFAULT_JUDGES).run(groups, times=times)


def write_report(
    report: EvalReport, prefix: Path, *, tail: str = ""
) -> tuple[Path, Path]:
    """报告落盘: 框架那两份 (`<前缀>.json` 与 `<前缀>.md`).

    - `<前缀>.json` —— 框架的规范形状 (issue 40 定的), `python -m CharAgent.eval
      compare` 读它. **一个业务键都不往里塞** (塞了就是两份口径混在一份文件里).
    - `<前缀>.md` —— 人看的: 框架那七块, 后面接上 `tail` (某次实验自己要追加的
      那一块). 两半必须在同一份文件里, 否则读报告的人会把装置 (或提示词格式) 的
      锅算到模型头上 —— 但 `tail` **不**进上面那份 JSON, 理由见第一条.

    Args:
        report: 跑完的那一批.
        prefix: 路径前缀 (目录不存在时自己建).
        tail: 接在 Markdown 尾巴上的一块; 空串 = 这次实验没有额外块 (prompt A/B).

    Returns:
        tuple[Path, Path]: (框架 JSON, Markdown).
    """
    prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = Path(f"{prefix}.json")
    markdown_path = Path(f"{prefix}.md")
    json_path.write_text(report.to_json(), encoding="utf-8")
    markdown_path.write_text(
        report.to_markdown() + ("\n" + tail if tail else ""), encoding="utf-8"
    )
    return json_path, markdown_path


def write_scoping(report: EvalReport, prefix: Path) -> Path:
    """第八块 (分类错误) 的机器可读版 → `<前缀>-scoping.json`.

    单开一份而不是塞进框架那份 JSON, 理由见 `write_report` 第一条: 框架那份是
    **两份报告之间可比**的形状, 多一个键就多一处漂移. 而票面要的「JSON 里含分类
    那一行」由这一份兑现.
    """
    path = Path(f"{prefix}-scoping.json")
    path.write_text(
        json.dumps(scoping_data(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_tools_report(report: EvalReport, prefix: Path) -> tuple[Path, ...]:
    """工具 A/B 的报告 (三份): 框架那两份 + 第八块的机器可读版.

    与 `write_report` 一样是「`(报告, 前缀) -> 落了哪些文件`」的一手 —— 于是两个
    实验在这张表里只差这一格 (`Experiment.write`), 而 `main` 不必知道哪一次多一块.
    """
    json_path, markdown_path = write_report(
        report, prefix, tail=scoping_section(report)
    )
    return json_path, markdown_path, write_scoping(report, prefix)


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


# 这个入口能跑的几次 A/B (表就是「有哪些实验」那件事的唯一出处: `--experiment`
# 的候选、默认出口、两臂、落盘那一手, 全在这几行里)
EXPERIMENTS: dict[str, Experiment] = {
    # 工具数量 A/B (issue 44): 多一块分类错误 —— 人读的追加在 Markdown 尾巴上, 机器
    # 读的单开一份 sidecar (票据要的「JSON 里含分类那一行」由它兑现)
    "tools": Experiment(
        arms=TOOLS_ARMS,
        out=REPORTS_DIR / "44-tools-ab",
        write=write_tools_report,
    ),
    # prompt A/B (issue 45): **没有额外块** —— 它的主指标 (回答合规 / markdown 强调)
    # 是框架那七块里就有的两列, 另立一块只会多出一处口径
    "prompt": Experiment(
        arms=PROMPT_ARMS,
        out=REPORTS_DIR / "45-prompt-ab",
        write=write_report,
    ),
}


def main(argv: Sequence[str] | None = None) -> int:
    """命令入口: 读环境 → 挑题 → 按 `--experiment` 跑两臂 → 落报告 → 报路径.

    Returns:
        int: 进程退出码 (0 正常; 1 参数或配置错; 130 被 Ctrl-C 打断).
    """
    load_root_env()
    use_utf8_stdio()
    args = build_parser().parse_args(argv)
    experiment = EXPERIMENTS[args.experiment]
    # 跑分这条链上的输出与重试提示都过打码 (威胁模型是「留痕去哪」, 见 subject.py)
    writer = redacting_writer(print, build_redactor())
    try:
        cases = pick_cases(load_cases(), args.cases)
        report = asyncio.run(
            run_ab(
                cases,
                times=args.times,
                arms=experiment.arms,
                model_name=args.model,
                writer=writer,
            )
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
    prefix = experiment.out if args.out is None else Path(args.out)
    paths = experiment.write(report, prefix)
    writer("报告落盘: " + " / ".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
