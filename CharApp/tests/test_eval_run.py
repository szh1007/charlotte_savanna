"""跑分入口 (issue 44): 挑题 · 两组的自变量 · 第八块 (分类错误) · 落盘.

这一页断的是交付物 6 里**不花钱的那一半** —— 报告真的落下来了, 而第八块真的把
「装置坏了的那几跑」单列出来了. 真模型那一趟 (几十到上百次问答) 不在 pytest 里:
这里给的是模型的注入缝 (`model_for`), 塞个 MockLLM 进去整条路就离线跑得通
(与全仓其他测试同一套做法).

三件事各自有一处容易静默出错的地方, 于是各有用例:

- **挑题**: 题号写错时静默少跑几道 —— 报告看起来照样完整 (那条 ValueError 是拦它的);
- **两组的自变量**: 两组若不止差一个开关, A/B 的结论就归因不了 —— 断言配置快照
  里那一行 (全挂 / 按题裁剪), 别的旋钮一模一样;
- **第八块**: 分类错误的那几跑若混进分数里, 报告看上去是「模型答错了」—— 断言它
  单列, 且「只数分类正确的题」那份分数与整体分得开.
"""

from __future__ import annotations

import json
from collections.abc import Callable
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
    ScopingRow,
    pick_cases,
    run_ab,
    scoping_data,
    scoping_section,
    write_report,
)
from CharApp.minimall.scoping import SCOPE_FULL, SCOPE_PRUNED

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
    """离线跑一遍两个组 (不触网也不打模型), 交回报告."""
    return await run_ab(
        cases,
        times=times,
        model_for=mock_models(),
        writer=lambda line: None,
    )


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
    differing = {
        key for key in set(full) | set(pruned) if full.get(key) != pruned.get(key)
    }
    assert differing == {"工具范围"}, f"两组不该差别的: {sorted(differing)}"


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


async def test_the_report_lands_as_three_files(tmp_path: Path) -> None:
    """三份文件都落下来: 框架的 JSON / 人看的 Markdown / 第八块的数据."""
    report = await offline_report((CORRECT_CASE, MISCLASSIFIED_CASE))
    prefix = tmp_path / "nested" / "44-tools-ab"

    json_path, markdown_path, scoping_path = write_report(report, prefix)

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
        model_for=mock_models(),
        writer=lines.append,
    )
    progress = [line for line in lines if line.startswith(f"[{SCOPE_FULL}]")]

    assert progress == [
        f"[{SCOPE_FULL}] 1/2 {CORRECT_CASE.id}",
        f"[{SCOPE_FULL}] 2/2 {MISCLASSIFIED_CASE.id}",
    ], f"进度行不对: {progress}"
    assert len(reports.group(SCOPE_FULL).attempts) == 2
