"""跑分入口 (issue 44 的工具 A/B + issue 45 的 prompt A/B): 挑题 · 自变量 · 落盘.

这一页断的是两次实验交付物里**不花钱的那一半** —— 报告真的落下来了, 第八块真的把
「装置坏了的那几跑」单列出来了, 而两次实验各自只拨一个开关. 真模型那一趟 (几十到
上百次问答) 不在 pytest 里: 这里给的是模型的注入缝 (`model_for`), 塞个 MockLLM
进去整条路就离线跑得通 (与全仓其他测试同一套做法).

四件事各自有一处容易静默出错的地方, 于是各有用例:

- **挑题**: 题号写错时静默少跑几道 —— 报告看起来照样完整 (那条 ValueError 是拦它的);
- **自变量**: 两组若不止差一个开关, A/B 的结论就归因不了 —— 表那一层 (每个实验
  只拨一个字段) 与跑出来那一层 (配置快照只差那一行) 各断一次;
- **第八块**: 分类错误的那几跑若混进分数里, 报告看上去是「模型答错了」—— 断言它
  单列, 且「只数分类正确的题」那份分数与整体分得开. 而 prompt 那次**没有**它
  (那两组没裁工具, 写上去就是说一件没发生过的事);
- **落盘**: 两次实验各落几份文件不同 (工具三份 / prompt 两份), 而框架那份 JSON
  两次都是同一套规范形状 (业务键一个都不进去).
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from CharAgent.eval import (
    REPORT_SCHEMA_VERSION,
    EvalCase,
    EvalReport,
    RunOutcome,
)
from CharAgent.model.protocol import ChatModel
from CharAgent.tests.mock_llm import (
    MockLLM,
    make_tool_call,
    text_response,
    tool_call_response,
)
from CharApp.eval.golden import load_cases
from CharApp.eval.run import (
    EXPERIMENTS,
    PROMPT_ARMS,
    PROMPT_BASELINE,
    PROMPT_VARIANT,
    Arm,
    ScopingRow,
    pick_cases,
    run_ab,
    scoping_data,
    scoping_section,
    write_tools_report,
)
from CharApp.minimall.config import MinimallConfigError
from CharApp.minimall.scoping import SCOPE_FULL, SCOPE_PRUNED
from CharApp.minimall.service import PROMPT_NAME

# 两道题就够: 一道分类器给得出正确工具 (product-01), 一道**故意分错** —— 题面只提
# 商品而期望的是 `place_order` (题集里没有这种题, 因为现在的分类器 20 道全对).
# 第八块的错误分支只能这样造出来 (见那一节里那条 singled out 的用例).
CORRECT_CASE = EvalCase(
    id="product-01",
    question="有什么 2000 块以下的手机推荐吗",
    expect_tools=("search_products",),
    meta={"scene": "product"},
)
MISCLASSIFIED_CASE = EvalCase(
    id="order-99",
    question="有什么 2000 块以下的手机推荐吗",
    expect_tools=("place_order",),
    meta={"scene": "order"},
)


def mock_models() -> Callable[[EvalCase], ChatModel]:
    """造模型的缝: 每一跑一个假大脑, 剧本**每次搜一件商品再作答**.

    每次都调 `search_products` 是刻意的: 分类对的那道 (`CORRECT_CASE`) 因此拿满分,
    而分错的那道 (`MISCLASSIFIED_CASE` 期望的是 `place_order`) 必然挂零 —— 第八块
    里那两个数才有差额可看 (那是这一页要断的东西).
    """

    def make(case: EvalCase) -> ChatModel:
        """这一跑的模型 (真模型那条路见 `build_groups` 的 `model_name`)."""
        return MockLLM.scripted(
            [
                tool_call_response(
                    make_tool_call("search_products", '{"keyword": "手机"}')
                ),
                text_response("给你找到几款, 最便宜的是红米 Note 13。"),
            ]
        )

    return make


async def offline_report(cases: tuple[EvalCase, ...], *, times: int = 1) -> EvalReport:
    """离线跑一遍工具 A/B 的两臂 (不触网也不打模型), 交回报告."""
    return await run_ab(
        cases,
        times=times,
        arms=EXPERIMENTS["tools"].arms,
        model_for=mock_models(),
        writer=lambda line: None,
    )


async def offline_prompt_report(
    cases: tuple[EvalCase, ...], *, times: int = 1
) -> EvalReport:
    """离线跑一遍 prompt A/B 的两臂 (同上; 剧本一样, 因为它不看提示词)."""
    return await run_ab(
        cases,
        times=times,
        arms=PROMPT_ARMS,
        model_for=mock_models(),
        writer=lambda line: None,
    )


def differing_keys(first: Mapping[str, Any], second: Mapping[str, Any]) -> set[str]:
    """两份配置快照之间不一样的那些键 —— A/B 的自变量就是它们."""
    return {
        key for key in set(first) | set(second) if first.get(key) != second.get(key)
    }


# 两次实验各自拨的那个开关 (`Arm` 的字段名) —— 「两组只差一个开关」那句承诺的底账
FLIPPED: dict[str, str] = {"tools": "prune_tools", "prompt": "prompt_version"}


# ---------------------------------------------------------------------------
# 挑题
# ---------------------------------------------------------------------------


def test_no_filter_keeps_the_whole_case_set() -> None:
    """`--cases` 不写 = 全跑 (题集里的顺序照旧)."""
    cases = load_cases()

    assert pick_cases(cases, "") == tuple(cases)


def test_the_filter_keeps_the_named_cases_in_that_order() -> None:
    """给了题号就只跑那几道, 顺序照**给的**顺序 (复现单题时要的是这一条)."""
    picked = pick_cases(load_cases(), "order-03, product-01")

    assert [case.id for case in picked] == ["order-03", "product-01"]


def test_an_unknown_case_id_is_reported_with_the_ones_that_exist() -> None:
    """题号写错当场报错 (静默少跑几道会让报告看着完整, 而它少了题)."""
    with pytest.raises(ValueError) as caught:
        pick_cases(load_cases(), "product-01,product-99")

    message = str(caught.value)
    assert "product-99" in message
    assert "product-01" in message, "报错里要列出真有的题号"


# ---------------------------------------------------------------------------
# 两组的自变量
# ---------------------------------------------------------------------------


async def test_the_two_groups_differ_only_in_the_pruning_switch() -> None:
    """两组跑**同一批题**, 而配置快照里只差「工具范围」那一行.

    这就是这次 A/B 的自变量 (票据验收: 报告头部要能一眼看出两组差在哪). 若哪天
    有人给其中一组多配一个旋钮, 这条会红 —— 那时结论就归因不了了.
    """
    cases = (CORRECT_CASE,)
    report = await offline_report(cases)
    full = report.group(SCOPE_FULL).attempts[0].facts.config
    pruned = report.group(SCOPE_PRUNED).attempts[0].facts.config

    assert report.group(SCOPE_FULL).cases == report.group(SCOPE_PRUNED).cases
    assert full["工具范围"] == "全挂"
    assert pruned["工具范围"] == "按题裁剪"
    differing = differing_keys(full, pruned)
    assert differing == {"工具范围"}, f"两组不该差别的: {sorted(differing)}"


async def test_the_prompt_arms_differ_only_in_the_version() -> None:
    """prompt A/B 的两臂: 配置快照里只差「提示词」那一行, 工具那一行两边都是全挂.

    与工具 A/B 那条同一个形状 (报告头部要能一眼看出两组差在哪), 而这条还多守一件
    事: **别顺手把工具也裁上** —— 两组各差两个开关的话, 那次差距归因不到版本上.
    """
    report = await offline_prompt_report((CORRECT_CASE,))
    baseline = report.group(PROMPT_BASELINE).attempts[0].facts.config
    variant = report.group(PROMPT_VARIANT).attempts[0].facts.config

    assert baseline["提示词"] == f"{PROMPT_NAME}/{PROMPT_BASELINE}"
    assert variant["提示词"] == f"{PROMPT_NAME}/{PROMPT_VARIANT}"
    assert baseline["工具范围"] == SCOPE_FULL == variant["工具范围"], "那次没裁工具"
    differing = differing_keys(baseline, variant)
    assert differing == {"提示词"}, f"两组不该差别的: {sorted(differing)}"


def test_each_experiment_flips_exactly_one_switch() -> None:
    """两次实验各自只拨**那一个**开关 —— 这是「两组只差一个开关」在表那一层的版本.

    上头两条断的是跑出来的配置快照, 这条断的是表本身: 谁哪天给某一臂多配一个旋钮
    (比如 prompt 那组顺手也裁上工具), 这里当场红, 而不必等跑完一次真模型才看出两组
    差了两处.

    `name` 不算开关 (两臂的名字本来就该不一样), 于是按字段名逐个比, 而不是比"几个
    字段不同".
    """
    assert set(EXPERIMENTS) == set(FLIPPED), "有实验没跟上这张表 (或者反过来)"

    for name, experiment in EXPERIMENTS.items():
        first, second = experiment.arms
        flipped = {
            field.name
            for field in dataclasses.fields(Arm)
            if field.name != "name"
            and getattr(first, field.name) != getattr(second, field.name)
        }
        assert flipped == {FLIPPED[name]}, f"{name} 那次拨的不是 {FLIPPED[name]}"


async def test_a_version_that_is_not_on_disk_fails_before_anything_runs() -> None:
    """钉的版本不在盘上 → **开跑前**就报, 而不是 120 跑一起记成 `BROKEN`.

    跑批器那条开跑前自检验的是「第一个被测对象装得出来吗」(`runner._probe`), 而提示词
    版本是**跑到第一题**才读的 —— 少了这一道, 一个写错的版本号会让整批记成 `BROKEN`
    (真因只在每一跑的 `error` 里), 而入口那句「报告落盘」照样报成功. 于是这条断的是
    "压根没开跑": 造模型那一步一次都没被调到.
    """
    asked: list[str] = []

    def model_for(case: EvalCase) -> ChatModel:
        """记下"有人来造模型了" —— 它被调到就说明已经开跑了."""
        asked.append(case.id)
        return MockLLM.scripted([text_response("好的")])

    arms = (Arm(name="v9", prompt_version="v9"), Arm(name="v3", prompt_version="v3"))

    with pytest.raises(MinimallConfigError) as caught:
        await run_ab((CORRECT_CASE,), times=1, arms=arms, model_for=model_for)

    assert "v9" in str(caught.value)
    assert asked == [], "该在造模型之前拦下 (跑批器那一步自检也算开跑)"


def test_each_experiment_writes_to_its_own_report_prefix() -> None:
    """两次实验各落各的名字 —— 报告要长期留在盘上, 而票与文档按名字引它们.

    钉住这两个名字是因为它们是**对外的** (issue 46 要把这几份数据写进文档, L5 之后
    还要回看): 改名不是重构, 是让那几处引用一起失效.
    """
    assert EXPERIMENTS["tools"].out.name == "44-tools-ab"
    assert EXPERIMENTS["prompt"].out.name == "45-prompt-ab"


def test_the_prompt_arms_are_named_after_their_versions() -> None:
    """组名就是版本号 —— 报告表头写着 `v3` / `v4`, 与配置快照里那一格对得上.

    对照臂必须是**指名**的一版 (不从清单读): 靠清单的话, 谁把默认换成 v4 的那一刻,
    两组就都成了 v4 —— 而报告上只是一次「两版成绩差不多」.
    """
    assert [arm.name for arm in PROMPT_ARMS] == [PROMPT_BASELINE, PROMPT_VARIANT]
    assert [arm.prompt_version for arm in PROMPT_ARMS] == [
        PROMPT_BASELINE,
        PROMPT_VARIANT,
    ]
    assert PROMPT_BASELINE != PROMPT_VARIANT, "对照组与实验组不该是同一版"


async def test_the_two_groups_run_the_same_number_of_attempts() -> None:
    """两组各跑 题数 x 次数 次 (少一边就没得比)."""
    report = await offline_report((CORRECT_CASE, MISCLASSIFIED_CASE), times=2)

    for name in (SCOPE_FULL, SCOPE_PRUNED):
        assert len(report.group(name).attempts) == 4, f"{name} 的跑次数不对"


# ---------------------------------------------------------------------------
# 第八块: 分类错误
# ---------------------------------------------------------------------------


def test_the_classifier_row_knows_what_is_missing() -> None:
    """一行的三栏: 分类器给的组、可见集、期望 —— 少给的那几个点名写出来."""
    good = ScopingRow.of(CORRECT_CASE)
    bad = ScopingRow.of(MISCLASSIFIED_CASE)

    assert good.correct and good.missing == ()
    assert good.groups == ("product",)
    assert not bad.correct
    assert bad.missing == ("place_order",)
    assert "place_order" in bad.verdict


async def test_the_block_singles_out_the_misclassified_cases() -> None:
    """第八块: 分错的那道单列, 而且**只有它**被点名."""
    report = await offline_report((CORRECT_CASE, MISCLASSIFIED_CASE))
    block = scoping_section(report)

    assert "## 8. 分类错误" in block
    assert "分类正确: 2 道里对了 1 道 (错 1 道)" in block
    assert "product-01 | product" in block, "分类对了的那道也在表里 (要对得出全貌)"
    assert "❌ 少了 place_order" in block
    assert "因分类错误而**工具选择必然不过**的跑次: 2" in block, "两组各一跑, 都要数上"


async def test_the_block_gives_the_scores_of_the_correctly_classified_cases() -> None:
    """「只数分类正确的题」的分数与整体分得开 —— 差额就是装置那一份.

    这一批里 `order-99` 是分错的 (期望 `place_order`, 而模型调的是 `search_products`),
    于是两组那两个数整体都掉到 50% (少调一个 + 多调一个), 而在分类正确的那一道上
    两个都是 100%. 括号里那个数才是 A/B 该比的.
    """
    report = await offline_report((CORRECT_CASE, MISCLASSIFIED_CASE))
    block = scoping_section(report)

    expected = "召回率 100.0% (整体 50.0%) · 准确率 100.0% (整体 50.0%)"
    for name in (SCOPE_FULL, SCOPE_PRUNED):
        assert f"- {name}: {expected}" in block


async def test_the_block_is_not_written_into_the_framework_json() -> None:
    """框架那份 JSON 是规范形状 (compare 读它) —— 业务那一块不混进去.

    第八块的机器可读版因此**单开一份** (见落盘那一节): 一份文件里混两套口径,
    是 `compare` 与读者两边都会踩的坑.
    """
    report = await offline_report((CORRECT_CASE,))
    block = scoping_section(report)

    assert "## 8. 分类错误" in block
    assert "分类错误" not in report.to_json(), "业务那一块不该混进框架的形状里"


async def test_the_block_data_carries_what_the_markdown_says() -> None:
    """第八块的数据与排版同源: 数一样, 逐题的结论也一样 (算一次, 两处看)."""
    report = await offline_report((CORRECT_CASE, MISCLASSIFIED_CASE))
    data = scoping_data(report)
    block = scoping_section(report)

    assert data["correct"] == 1 and data["wrong"] == 1
    assert data["doomed_attempts"] == 2
    verdicts = {item["case_id"]: item["verdict"] for item in data["cases"]}
    assert verdicts[CORRECT_CASE.id] == "✅"
    assert "place_order" in verdicts[MISCLASSIFIED_CASE.id]
    for verdict in verdicts.values():
        assert verdict in block, "数据里那句话要真出现在排版里"
    assert data["scores"][SCOPE_FULL]["召回率"] == 1.0
    assert data["scores"][SCOPE_FULL]["overall"]["召回率"] == 0.5


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------


async def test_the_tools_report_lands_as_three_files(tmp_path: Path) -> None:
    """工具 A/B 落三份: 框架的 JSON / 人看的 Markdown / 第八块的数据."""
    report = await offline_report((CORRECT_CASE, MISCLASSIFIED_CASE))
    prefix = tmp_path / "nested" / "44-tools-ab"

    json_path, markdown_path, scoping_path = write_tools_report(report, prefix)

    assert json_path.exists() and markdown_path.exists() and scoping_path.exists(), (
        "目录不存在时要自己建"
    )
    data: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == REPORT_SCHEMA_VERSION
    assert [group["name"] for group in data["groups"]] == [SCOPE_FULL, SCOPE_PRUNED]
    text = markdown_path.read_text(encoding="utf-8")
    assert "## 1. 配置快照" in text, "框架那七块还在"
    assert text.rstrip().endswith("(那些题上的分数不计入结论)"), "第八块在最后"
    scoping: dict[str, Any] = json.loads(scoping_path.read_text(encoding="utf-8"))
    assert scoping["wrong"] == 1, "分类那一行在 JSON 里也读得到 (票据交付物 6)"
    assert {item["case_id"] for item in scoping["cases"]} == {
        CORRECT_CASE.id,
        MISCLASSIFIED_CASE.id,
    }


async def test_the_prompt_report_lands_as_two_files_without_the_extra_block(
    tmp_path: Path,
) -> None:
    """prompt A/B 只落两份, 且 Markdown 尾巴上**没有**第八块.

    那一块讲的是**分类器** (裁剪组才有的东西); prompt 那两臂压根没裁, 照抄过去就是
    在报告里说一件没发生过的事 (票据 2026-09-28 补注 ④ 点的正是这一条). 于是这里断
    三样: 少一份文件、少一块正文、组名就是版本号.
    """
    report = await offline_prompt_report((CORRECT_CASE,))
    prefix = tmp_path / "45-prompt-ab"

    paths = EXPERIMENTS["prompt"].write(report, prefix)

    assert [path.name for path in paths] == ["45-prompt-ab.json", "45-prompt-ab.md"]
    assert not (tmp_path / "45-prompt-ab-scoping.json").exists(), "那次实验没有第八块"
    data: dict[str, Any] = json.loads(paths[0].read_text(encoding="utf-8"))
    assert [group["name"] for group in data["groups"]] == [
        PROMPT_BASELINE,
        PROMPT_VARIANT,
    ]
    text = paths[1].read_text(encoding="utf-8")
    assert "## 1. 配置快照" in text and "## 7. " in text, "框架那七块还在"
    assert "## 8. 分类错误" not in text


async def test_the_attempts_are_all_finished_in_the_offline_run() -> None:
    """离线那一跑每一跑都跑完了 (判据才有分母) —— 挡住了「装置坏了看不出来」."""
    report = await offline_report((CORRECT_CASE,))

    outcomes = [attempt.facts.outcome for attempt in report.group(SCOPE_FULL).attempts]
    assert outcomes == [RunOutcome.COMPLETED]


async def test_the_progress_skips_the_pre_flight_probe() -> None:
    """进度那一行从 1 数起 —— 开跑前那一次自检不算一跑.

    跑批器为每组先造一个对象看装不装得起来 (`runner._probe`), 它也是走工厂的.
    不扣掉它的话, 真模型那一趟的屏幕上会出现「3/2」这种自相矛盾的数字 (一个
    「跑多了」的错觉), 而这条用例把它钉在「1/2 起、到 2/2 止」.
    """
    lines: list[str] = []
    cases = (CORRECT_CASE, MISCLASSIFIED_CASE)
    reports = await run_ab(
        cases,
        times=1,
        arms=EXPERIMENTS["tools"].arms,
        model_for=mock_models(),
        writer=lines.append,
    )
    progress = [line for line in lines if line.startswith(f"[{SCOPE_FULL}]")]

    assert progress == [
        f"[{SCOPE_FULL}] 1/2 {CORRECT_CASE.id}",
        f"[{SCOPE_FULL}] 2/2 {MISCLASSIFIED_CASE.id}",
    ], f"进度行不对: {progress}"
    assert len(reports.group(SCOPE_FULL).attempts) == 2
