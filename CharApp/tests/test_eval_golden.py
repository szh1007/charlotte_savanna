"""题集 (issue 42): 20 道题读得进来, 写坏的地方当场拦下.

这一页断的是验收第 1 条与第 5 条 —— 题能加载并通过校验 (含「期望工具名必须真实
存在」这条: 写错名字的题会在跑分时才炸, 太晚), 以及题面里搜不到敏感值原文.

负例那几条**都用临时目录** (`load_cases(tmp_path)`): 它们要验的是**校验本身**
管用, 而不是去改真题集 (真题集有一条用例在守着它的形状, 见前几条).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from CharAgent.eval import EvalConfigError
from CharApp.eval.fixtures import BIG_CART_BUYER, BUYER_ID, SENSITIVE_VALUES
from CharApp.eval.golden import SCENES, load_cases

# 票据定的题数 (issue 42 §一). 写死是**故意**的: 两个 A/B 共用这一批题, 题数变了
# 是件该被人看见的事 (而不是测试跟着悄悄变).
CASE_COUNT = 20


def write_case(directory: Path, body: str, *, name: str = "cases.yaml") -> Path:
    """往临时目录里放一页题 (负例用)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 真题集: 形状与分布
# ---------------------------------------------------------------------------


def test_the_whole_casebook_loads() -> None:
    """20 道题全部读得进来, 题号唯一."""
    cases = load_cases()

    assert len(cases) == CASE_COUNT
    assert len({case.id for case in cases}) == CASE_COUNT


def test_the_six_scenes_are_evenly_covered() -> None:
    """六个场景各 3-4 条 (票据 §一 的分布), 且场景都进得了 `meta`."""
    counts = Counter(case.meta["scene"] for case in load_cases())

    assert set(counts) == set(SCENES), "每个场景都得有题, 也不该冒出计划外的场景"
    assert all(3 <= number <= 4 for number in counts.values()), counts
    assert sum(counts.values()) == CASE_COUNT


def test_a_case_id_names_its_scene_and_a_serial() -> None:
    """题号是「场景-序号」(`product-01`) —— 报告按它排序与引用 (决策 1)."""
    for case in load_cases():
        prefix, _, serial = case.id.partition("-")
        assert prefix == case.meta["scene"], f"{case.id} 的题号与场景对不上"
        assert serial.isdigit() and len(serial) == 2, f"{case.id} 的序号该是两位"


def test_exactly_one_case_expects_a_suspension_and_one_a_refusal() -> None:
    """两条「殊途」的题各一条: 挂起那条走 issue 43 的确认流程, 拒绝那条撞护栏.

    写死「各一条」是因为它们的用处是**各验一件事** —— 挂起链与护栏那条金额上限.
    多出来的同一类题没有新信息, 少一条则那件事没人验 (决策 2 要的正是那条超限的).
    """
    cases = load_cases()
    suspended = [case.id for case in cases if case.meta["expect_suspend"]]
    refused = [case.id for case in cases if case.meta.get("expect_refusal")]

    assert len(suspended) == 1, suspended
    assert len(refused) == 1, refused
    assert cases[0].meta["expect_suspend"] is False, "默认值要落地 (不是缺字段)"


def test_the_refused_case_asks_for_another_buyer() -> None:
    """超限那一单必须换一只车 —— 默认样本那只车撞不到 5000 上限.

    这条把两个字段绑在一起 (`expect_refusal` 与 `buyer_id`): 只写前者而不换车,
    那一题会以「挂起」而不是「被拒」收场, 而报告上看不出是题写错了.
    """
    [case] = [item for item in load_cases() if item.meta.get("expect_refusal")]

    # 精确等于那只贵车买家, 不是「随便换个别的值」: 换错人拿到的是样本那只 2598
    # 的车, 那一单会转成**挂起**而不是被拒 —— 挂起不进汇总, 于是决策 2 要测的那件
    # 事静默消失, 而全仓用例照样全绿
    assert case.meta["buyer_id"] == BIG_CART_BUYER
    assert case.meta["buyer_id"] != BUYER_ID, "样本那只车撞不到 5000 那条线"
    assert case.meta["expect_refusal"] in case.expect_tools


def test_no_question_carries_a_sensitive_value() -> None:
    """题面里不许出现敏感值原文 —— 题面泄了题, 判据就会误判自己设的题.

    题面写「我的地址是文三路 100 号」的话, 模型把它复述一遍就是**正常的**: 那正是
    买家自己说的话. 于是合规判据会抓一个假的违规 —— 这条用例把这种题拦在题集里.
    """
    for case in load_cases():
        for name, value in SENSITIVE_VALUES.items():
            assert value not in case.question, (
                f"{case.id} 的题面里有 {name} 的原文: {case.question!r}"
            )


# ---------------------------------------------------------------------------
# 校验: 写坏的题当场拦下
# ---------------------------------------------------------------------------


def test_a_misspelled_tool_name_is_caught_at_load(tmp_path: Path) -> None:
    """期望工具名写错一个字母 → 读的那一刻就报 (不是跑分时变成「召回率 0.6」)."""
    write_case(
        tmp_path,
        "- id: product-01\n"
        "  scene: product\n"
        "  question: 有手机吗\n"
        "  expect_tools: [search_product]\n",
    )

    with pytest.raises(EvalConfigError, match="search_product"):
        load_cases(tmp_path)


def test_an_unknown_scene_is_caught(tmp_path: Path) -> None:
    """场景不在白名单 (写错字 = 这条题在分布统计里凭空消失)."""
    write_case(
        tmp_path,
        "- id: prodcut-01\n  scene: prodcut\n  question: 有手机吗\n",
    )

    with pytest.raises(EvalConfigError, match="prodcut"):
        load_cases(tmp_path)


def test_a_duplicate_id_is_caught_across_files(tmp_path: Path) -> None:
    """题号重复 —— 哪怕两条题在两页不同的文件里 (逐题表按题号当行标签)."""
    body = "- id: product-01\n  scene: product\n  question: 有手机吗\n"
    write_case(tmp_path, body, name="a.yaml")
    write_case(tmp_path, body, name="b.yaml")

    with pytest.raises(EvalConfigError, match="题号重复"):
        load_cases(tmp_path)


def test_missing_fields_are_caught(tmp_path: Path) -> None:
    """必填字段缺一个 (题号 / 场景 / 题面)."""
    write_case(tmp_path, "- id: product-01\n  scene: product\n")

    with pytest.raises(EvalConfigError, match="question"):
        load_cases(tmp_path)


def test_a_non_boolean_suspend_flag_is_caught(tmp_path: Path) -> None:
    """`expect_suspend` 写了字符串 —— 真值判断会把它当成 true, 而这没人看得出来."""
    write_case(
        tmp_path,
        "- id: order-01\n  scene: order\n  question: 下单\n  expect_suspend: 是的\n",
    )

    with pytest.raises(EvalConfigError, match="expect_suspend"):
        load_cases(tmp_path)


def test_bad_meta_keys_are_caught(tmp_path: Path) -> None:
    """`meta` 里跑分自己认的那两个键写坏 (`buyer_id` 不是整数 / 工具名不存在)."""
    write_case(
        tmp_path,
        "- id: order-01\n"
        "  scene: order\n"
        "  question: 下单\n"
        "  meta: {buyer_id: '4', expect_refusal: place_orde}\n",
    )

    with pytest.raises(EvalConfigError) as caught:
        load_cases(tmp_path)

    message = str(caught.value)
    assert "buyer_id" in message and "place_orde" in message, (
        "两处都要报出来 —— 题是人写的, 别让他们改一处跑一次"
    )


def test_one_typo_reports_exactly_one_problem(tmp_path: Path) -> None:
    """一处写坏只报一处 —— 好题被一起报成坏的, 比不报还费事.

    校验是给**写题的人**看的: 报出来的每一行他都得去核对一遍. 报错里混进几条本来
    没问题的题, 他就得把整页重读一遍才能确定该改哪一行.
    """
    write_case(
        tmp_path,
        "- id: product-01\n  scene: product\n  question: 有手机吗\n"
        "  expect_tools: [search_product]\n"
        "- id: product-02\n  scene: product\n  question: 有电脑吗\n"
        "  expect_tools: [search_products]\n",
    )

    with pytest.raises(EvalConfigError) as caught:
        load_cases(tmp_path)

    message = str(caught.value)
    assert "product-01" in message, "坏的那条要点名"
    assert "product-02" not in message, "好的那条不该被牵连"


def test_a_page_that_is_not_a_list_is_caught(tmp_path: Path) -> None:
    """顶层不是「一串题」(比如整页写成映射)."""
    write_case(tmp_path, "product-01:\n  scene: product\n")

    with pytest.raises(EvalConfigError, match="顶层"):
        load_cases(tmp_path)


def test_a_broken_yaml_page_is_caught(tmp_path: Path) -> None:
    """YAML 本身读不出来 (缩进写坏)."""
    write_case(tmp_path, "- id: product-01\n   scene: product\n  question: 你好\n")

    with pytest.raises(EvalConfigError, match="读不出来"):
        load_cases(tmp_path)


def test_an_empty_directory_is_an_error_not_an_empty_batch(tmp_path: Path) -> None:
    """一页题都没有 → 报错, 不是交出一批零题的「成功」.

    零题跑分会得到一份每块都空着的报告, 而它看上去像是「跑完了」.
    """
    with pytest.raises(EvalConfigError, match="一页题都没有"):
        load_cases(tmp_path)
