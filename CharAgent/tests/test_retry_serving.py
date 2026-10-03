"""运行级台账测试 (difficulties #14): 谁服务过这一趟, 以及它怎么被读出来.

台账本身很小, 但有三条容易做错的地方, 各钉一条:
- **同名只记一次**, 顺序按首次出场 (组合名的可读性靠它);
- **作用域可嵌套且出块还原** (续跑那段嵌在主段里, 出块后外层读到的仍是自己那本);
- **没开作用域时写入是空操作** (模型层不该关心有没有人在记账).

记账的口径 (单模型记名 / 混用记组合名 / 空手给默认名) 在 test_retry_failover.py
那边按「真跑一遍」验, 这里只测这本台账的读写.
"""

from __future__ import annotations

from CharAgent.retry import ServingRecord, current_serving, serving_scope
from CharAgent.retry.serving import note_serving


def test_a_fresh_record_has_nobody_and_falls_back_to_the_given_name() -> None:
    record = ServingRecord()

    assert record.names == ()
    assert record.model_name() is None
    assert record.model_name("deepseek-flash") == "deepseek-flash"


def test_names_are_deduped_and_kept_in_first_seen_order() -> None:
    record = ServingRecord()

    record.note("备")
    record.note("主")
    record.note("备")

    assert record.names == ("备", "主")
    assert record.model_name() == "备+主"


def test_one_name_is_recorded_as_itself() -> None:
    record = ServingRecord()
    record.note("gpt-6-luna")

    assert record.model_name("配置的名字") == "gpt-6-luna"


def test_the_scope_restores_the_outer_record_on_exit() -> None:
    with serving_scope() as outer:
        note_serving("主")
        with serving_scope() as inner:
            note_serving("备")
            assert inner.names == ("备",)
        assert current_serving() is outer, "出块之后回到外面那一本"
        assert outer.names == ("主",)


def test_writing_without_a_scope_is_a_no_op() -> None:
    assert current_serving() is None
    note_serving("主")  # 不该炸, 也没地方记
    assert current_serving() is None


def test_the_outer_scope_is_restored_even_when_the_body_raises() -> None:
    """一段运行炸了也得把作用域还回去 —— 否则下一次运行会记到别人的账上."""
    try:
        with serving_scope():
            raise RuntimeError("这一趟炸了")
    except RuntimeError:
        pass

    assert current_serving() is None
