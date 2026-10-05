"""Golden set: 加载校验 + 真题库的自检.

题是**数据** (加一条 badcase 就是加一段 YAML), 所以「格式错」必须在加载期就炸掉 ——
最贵的错误是 gold 列名拼错: 它不会报错, 只会让召回率永远差一截, 而人看不出是题写错了.

最后两个用例对着**真题库**与本地 `conf/meta_config.yaml` 交叉核对标识符 ——
后者是本地私有配置, 缺席时跳过 (干净机器上仓库里没有它).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.eval.golden import (
    CATEGORIES,
    GoldenSetError,
    load_golden_set,
    parse_cases,
)

PROJECT_ROOT = Path(__file__).parents[1]
GOLDEN_SET_PATH = PROJECT_ROOT / "app" / "eval" / "golden_set.yaml"
META_CONFIG_PATH = PROJECT_ROOT / "conf" / "meta_config.yaml"


def _case(**overrides: object) -> dict:
    base = {
        "id": "single-01",
        "category": "single_table",
        "question": "一共有多少笔订单",
        "gold_tables": ["fact_order"],
        "gold_columns": ["fact_order.order_id"],
        "gold_sql": "SELECT COUNT(*) FROM fact_order",
        "notes": "口径: 全部订单",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 加载与校验
# ---------------------------------------------------------------------------


def test_parses_a_minimal_case_with_defaults() -> None:
    (case,) = parse_cases([_case()])

    assert case.id == "single-01"
    assert case.category == "single_table"
    assert case.gold_metric is None, "不写 gold_metric 即视为无指标题"
    assert case.eex is False, "不写 eex 即视为不做结果比对"


def test_every_category_is_covered_by_the_enum() -> None:
    """覆盖面是题目的骨架 —— 枚举在这里, 题库必须落进这些格子."""
    assert set(CATEGORIES) == {
        "single_table",
        "join",
        "time_range",
        "relative_time",
        "value_filter",
        "metric_alias",
        "no_metric",
    }


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"id": ""}, "id"),
        ({"category": "ambiguous"}, "category"),
        ({"gold_tables": []}, "gold_tables"),
        ({"gold_columns": []}, "gold_columns"),
        ({"gold_columns": ["order_amount"]}, "表名.列名"),
        ({"gold_sql": ""}, "gold_sql"),
        ({"notes": ""}, "notes"),
    ],
)
def test_rejects_invalid_cases(overrides: dict, needle: str) -> None:
    with pytest.raises(GoldenSetError) as excinfo:
        parse_cases([_case(**overrides)])

    assert needle in str(excinfo.value)


def test_rejects_unknown_keys_catching_typos() -> None:
    """`gold_cols` 这种拼错必须当场炸, 不能静默当没写."""
    with pytest.raises(GoldenSetError) as excinfo:
        parse_cases([_case(gold_cols=["fact_order.order_id"])])

    assert "gold_cols" in str(excinfo.value)


def test_rejects_duplicate_ids() -> None:
    with pytest.raises(GoldenSetError) as excinfo:
        parse_cases([_case(), _case()])

    assert "single-01" in str(excinfo.value)


def test_eex_marker_round_trips() -> None:
    (case,) = parse_cases([_case(eex=True, gold_metric="GMV")])

    assert case.eex is True
    assert case.gold_metric == "GMV"


# ---------------------------------------------------------------------------
# 真题库自检 (格式层)
# ---------------------------------------------------------------------------


def test_real_golden_set_parses_within_size_and_ids_are_unique() -> None:
    cases = load_golden_set(GOLDEN_SET_PATH)

    assert 30 <= len(cases) <= 50, "题库规模按票据定在 30~50 题"
    assert len({case.id for case in cases}) == len(cases)
    assert {case.category for case in cases} <= set(CATEGORIES)
    assert len({case.category for case in cases}) == len(CATEGORIES), (
        "七类覆盖面每类都要有题"
    )
    assert sum(1 for case in cases if case.eex) >= 5, "EEX 至少留几道能手算的"


def test_every_question_is_non_trivial() -> None:
    for case in load_golden_set(GOLDEN_SET_PATH):
        assert len(case.question) >= 4, f"{case.id} 的题面太短"
        assert case.notes.strip(), f"{case.id} 缺口径说明"


# ---------------------------------------------------------------------------
# 真题库自检 (标识符层): 与本地元数据声明交叉核对
# ---------------------------------------------------------------------------

needs_meta_config = pytest.mark.skipif(
    not META_CONFIG_PATH.exists(),
    reason="本地私有 conf/meta_config.yaml 缺席 (干净机器上正常)",
)


@needs_meta_config
def test_gold_identifiers_exist_in_meta_config() -> None:
    payload = yaml.safe_load(META_CONFIG_PATH.read_text(encoding="utf-8"))

    tables = {table["name"] for table in payload["tables"]}
    columns = {
        f"{table['name']}.{column['name']}"
        for table in payload["tables"]
        for column in table["columns"]
    }
    metrics = {metric["name"] for metric in payload.get("metrics", [])}

    for case in load_golden_set(GOLDEN_SET_PATH):
        unknown_tables = set(case.gold_tables) - tables
        unknown_columns = set(case.gold_columns) - columns
        assert not unknown_tables, (
            f"{case.id} 的 gold_tables 有库里没有的表: {unknown_tables}"
        )
        assert not unknown_columns, (
            f"{case.id} 的 gold_columns 有库里没有的列: {unknown_columns}"
        )
        if case.gold_metric is not None:
            assert case.gold_metric in metrics, f"{case.id} 的 gold_metric 未定义"
