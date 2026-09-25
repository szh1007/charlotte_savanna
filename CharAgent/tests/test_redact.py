"""日志脱敏: 通用规则 + 按声明的字段路径打码 (difficulties #26, issue 29).

这个包是「写出去之前先打码」的那把尺子, 测的就三件事:

1. **该打的打了**: 四类跨业务的形状 (手机号 / 邮箱 / 身份证 / 银行卡) 各自一条, 而
   且**中文紧挨着数字**也要打得中 (中文在 `re` 里也算 `\\w`, 用 `\\b` 写边界的规则
   会在中文日志里静默失效 —— 这条专门钉住那个坑).
2. **不该猜的不猜**: 订单号 (本项目自己的 24 位编号)、短号、人名一律不动 ——
   「兜不住的如实留着」是这一层的**纪律**, 不是它没做.
3. **有结构的按声明打**: 字段路径命中就打码 / 抹除, 递进来的原件一个字节都不动.

样本用**真实形状**: 订单号抄的是 `app/minimall/utils.py` 那个
`YYYYMMDDHHMMSS + 买家 ID + 4 位随机` (24 位), 手机号抄的是商城样本里的
`13800000003` —— 样本一旦比真数据宽松, 测试就会在真实链路上放行本该红的东西.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from CharAgent.redact import (
    MASK_RULES,
    WIPE,
    WIPED,
    Mask,
    RedactConfigError,
    RuleRedactor,
    mask_text,
)

# 白盒: `_middle` 是「留头留尾」那条策略本身, 而今天的四条规则都够长 —— 想验那条
# 策略只有自己造一条短规则 (见下面那条用例的理由)
from CharAgent.redact.rules import _middle

# 真机上 minimall 的订单号就是长这样 (24 位纯数字, 见模块 docstring)
ORDER_NO = "202609191230450000031234"

# 商城样本里的手机号 (`CharApp/tests/conftest.py` 的 PROFILE)
PHONE = "13800000003"


# ---------------------------------------------------------------------------
# 自由文本: 四把尺子 + 两条边界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "raw", "masked"),
    [
        ("phone", PHONE, "138****0003"),
        ("email", "buyer3@example.com", "b*****@example.com"),
        ("id_card", "110101199003071234", "1101**********1234"),
        ("bank_card", "6222021234567890123", "6222***********0123"),
    ],
)
def test_each_rule_masks_its_own_shape(rule: str, raw: str, masked: str) -> None:
    """四类形状各一条: 打码留「形状」不留「值」(留头留尾是为了分得清是不是同一个)."""
    assert mask_text(raw) == masked
    assert raw not in mask_text(raw)


def test_a_phone_glued_to_chinese_is_still_masked() -> None:
    """手机号紧贴着汉字也要打得中 (这条钉住「边界不用 `\\b`」那个选择).

    为什么值得单列: `\\b` 在中文日志里**静默失效** —— 中文算 `\\w`, 于是
    `手机号13800000003` 里汉字与数字之间没有词边界, 规则一声不响地放过. 这是那种
    「测试不写就永远发现不了」的坑 (真机上日志正文恰恰是中文).
    """
    assert mask_text(f"手机号{PHONE}打不通") == "手机号138****0003打不通"


def test_a_digit_run_that_is_neither_a_phone_nor_a_card_is_left_alone() -> None:
    """夹在别的数字里的一串数字不动 (前后顾盼挡住"从中间截一段出来打码")."""
    assert mask_text("单号 123456789012") == "单号 123456789012"


def test_the_order_number_is_not_guessed() -> None:
    """订单号 (本项目自己的编号) 一个字都不动 —— 订单号不进日志靠**不打它**.

    规则只管跨业务的形状 (手机号 / 邮箱 / 身份证 / 银行卡); 订单号的形状是本项目
    的实现细节, 给它写规则就是 #26 说的「碰运气」: 猜错了会误伤真实数据.
    """
    line = f"订单 {ORDER_NO} 已发货, 收货人 张三"

    assert mask_text(line) == line


def test_several_values_in_one_line_are_all_masked() -> None:
    """一行里出现多个敏感值: 每个都打 (漏掉一个就等于没打)."""
    line = f"买家 {PHONE} / {PHONE} 用 buyer3@example.com 下的单"

    masked = mask_text(line)

    assert PHONE not in masked
    assert "buyer3@example.com" not in masked


def test_masking_twice_changes_nothing() -> None:
    """打过码的文字再打一遍还是那样 (打码不该把 `*` 又当成新形状)."""
    once = mask_text(f"手机号{PHONE}")

    assert mask_text(once) == once


def test_an_empty_line_is_untouched() -> None:
    """空串 / 没有敏感值的句子逐字返回 (这一层不做别的加工)."""
    assert mask_text("") == ""
    assert mask_text("今天天气不错") == "今天天气不错"


# ---------------------------------------------------------------------------
# 有结构的数据: 按声明的字段路径打码
# ---------------------------------------------------------------------------


def test_a_declared_path_is_masked_at_its_own_level() -> None:
    """声明 `customer.phone` 就打那一格 (别的格子不动)."""
    redactor = RuleRedactor(fields={"customer.phone": "phone"})
    data = {"customer": {"phone": PHONE, "name": "张三"}, "other": PHONE}

    masked = redactor.redact_fields(data)

    assert masked["customer"]["phone"] == "138****0003"
    assert masked["customer"]["name"] == "张三"
    assert masked["other"] == PHONE, "没声明的路径不该被动"


def test_one_star_matches_exactly_one_level() -> None:
    """`*` 只管一层 (与 glob 同义): `*.phone` 是「某一层里的 phone」, 穿透不过去."""
    redactor = RuleRedactor(fields={"*.phone": "phone"})
    data: dict[str, Any] = {
        "customer": {"phone": PHONE},
        "deeper": {"a": {"phone": PHONE}},
        "phone": PHONE,
    }

    masked = redactor.redact_fields(data)

    assert masked["customer"]["phone"] == "138****0003"
    assert masked["deeper"]["a"]["phone"] == PHONE, "一层通配不该穿两层"
    assert masked["phone"] == PHONE, "`*.phone` 说的是「某一层.phone」, 不是顶层那个"


def test_two_stars_reach_any_depth() -> None:
    """`**` 管任意层 (含它自己那一层): 支付密码这种「哪一层都不许留」的走它."""
    redactor = RuleRedactor(fields={"**.payment_password": WIPE})
    data: dict[str, Any] = {
        "payment_password": "hunter2",
        "order": {"payment_password": "hunter2"},
        "deeper": {"a": {"b": {"payment_password": "hunter2"}}},
    }

    masked = redactor.redact_fields(data)

    assert masked["payment_password"] == WIPED
    assert masked["order"]["payment_password"] == WIPED
    assert masked["deeper"]["a"]["b"]["payment_password"] == WIPED


def test_the_wildcard_walks_into_a_list() -> None:
    """列表里的每一个元素也走 (地址列表那种形状: `addresses.*.phone`)."""
    redactor = RuleRedactor(fields={"addresses.*.phone": "phone"})
    data: dict[str, Any] = {
        "addresses": [{"phone": "13800000003"}, {"phone": "13900000004"}],
        "total": 2,
    }

    masked = redactor.redact_fields(data)

    assert [item["phone"] for item in masked["addresses"]] == [
        "138****0003",
        "139****0004",
    ]


def test_wipe_replaces_a_value_of_any_shape() -> None:
    """`WIPE` 不看形状: 字符串 / 数字 / 整棵子树都整段换掉."""
    redactor = RuleRedactor(fields={"a": WIPE, "b": WIPE, "c": WIPE})

    masked = redactor.redact_fields({"a": "x", "b": 42, "c": {"nested": "y"}})

    assert masked == {"a": WIPED, "b": WIPED, "c": WIPED}


def test_a_number_is_masked_by_its_text_form() -> None:
    """值是数字也打得住 (数字手机号按字符串形式打) —— 日志要的是「打过码了」."""
    redactor = RuleRedactor(fields={"phone": "phone"})

    masked = redactor.redact_fields({"phone": 13800000003})

    assert masked["phone"] == "138****0003"


def test_a_value_that_does_not_look_like_the_declared_shape_is_left_alone() -> None:
    """声明成手机号但值不长那样: 一个字都不动 (规则打的是**形状**, 不是那一格).

    这条是这一层的诚实处: 「这格是手机号」是业务的声明, 而打码按形状走 —— 声明错了
    的时候, 表现是「没打」而不是「打出一串看着像打过码的字符」. 想让这一格**一定**
    不留原文, 该用 `WIPE` (那个不看形状).
    """
    redactor = RuleRedactor(fields={"phone": "phone"})

    assert redactor.redact_fields({"phone": "12"}) == {"phone": "12"}


def test_a_match_shorter_than_the_kept_ends_is_masked_whole() -> None:
    """留头留尾装不下时**整段打掉** (规则表的防线: 以后加短形状的规则也不会漏).

    今天这四条都够长 (最短的手机号 11 位 > 留头 3 + 留尾 4), 所以这条拿一条自造的
    短规则来钉**策略**本身 —— 防的是「以后有人加一条 4 位规则, 结果中间那段算成
    负数、头尾切片叠在一起把原文漏出来」.
    """
    short = Mask("short", re.compile(r"\d{3}"), _middle(2, 2))

    assert short.apply("123") == "***"


def test_the_input_is_never_touched() -> None:
    """递进来的那份数据一个字节都不动 (它常常是还要落库 / 还要发出去的原始数据)."""
    redactor = RuleRedactor(fields={"**.phone": "phone", "**.payment_password": WIPE})
    data: dict[str, Any] = {
        "phone": PHONE,
        "order": {"phone": PHONE, "payment_password": "hunter2"},
        "addresses": [{"phone": PHONE}],
    }
    before = {
        "phone": PHONE,
        "order": {"phone": PHONE, "payment_password": "hunter2"},
        "addresses": [{"phone": PHONE}],
    }

    masked = redactor.redact_fields(data)

    assert data == before, "原件被改了 (打码动的是副本)"
    assert masked is not data
    assert masked["order"] is not data["order"], "改过的那条路上不该共用同一个 dict"


def test_no_declaration_means_fields_are_left_alone() -> None:
    """没声明就没有名单: 字段这一半什么也不做 (自由文本那一半照常)."""
    redactor = RuleRedactor()
    data: dict[str, Any] = {"phone": PHONE, "payment_password": "hunter2"}

    assert redactor.redact_fields(data) == data
    assert redactor.redact_text(f"手机号{PHONE}") == "手机号138****0003"


@pytest.mark.parametrize("rule", ["sms", "", "PHONE", "id-card"])
def test_an_unknown_rule_name_is_refused_at_construction(rule: str) -> None:
    """规则名认不出: 造对象的时候就报 (脱敏失效的症状是**静默地漏**, 不能等).

    这条规矩与 `ensure_pricing_ready` 同源: 配置类错误在装配那一刻说清, 而不是等
    日志真要写的时候才发现打码没生效 —— 那时值已经出去了.
    """
    with pytest.raises(RedactConfigError, match="不认识的规则名"):
        RuleRedactor(fields={"phone": rule})


@pytest.mark.parametrize("bad", [{"phone": 123}, {"phone": None}])
def test_a_rule_name_that_is_not_a_string_is_refused(bad: dict[str, Any]) -> None:
    """规则名不是字符串: 同样当场报 (报错信息里列得出认识的那几个)."""
    with pytest.raises(RedactConfigError, match="不认识的规则名"):
        RuleRedactor(fields=bad)


def test_a_path_that_ends_with_a_wildcard_is_refused_at_construction() -> None:
    """路径最后一段是通配符: 当场报 (`{"a.**": WIPE}` 这种「整个子树」的写法不在口径里).

    为什么值得一条: 放过去它不会静默失效, 而是**崩** —— 那一格先被抹成一个字符串,
    接着代码拿它当字典改, 冒出来的是看不懂的 TypeError (`string indices must be
    integers`). 通配符是用来找名字的, 名字得写在最后一段.
    """
    for path in ("**", "*", "a.**", "a.*"):
        with pytest.raises(RedactConfigError, match="最后一段不能是通配符"):
            RuleRedactor(fields={path: WIPE})


@pytest.mark.parametrize("path", ["", "   ", "a..b", "a."])
def test_a_bad_path_is_refused_at_construction(path: str) -> None:
    """路径空着 / 多打个点: 当场报 (这种声明写错了就是白写)."""
    with pytest.raises(RedactConfigError, match="字段路径"):
        RuleRedactor(fields={path: "phone"})


def test_every_rule_in_the_table_has_a_name_and_a_shape() -> None:
    """规则表自身的形状: 名字与 key 一致, 每条都真的能在文字里认出点什么.

    (防的是「加了第五条规则却忘了填 pattern」这种半成品 —— 那种规则会静默地什么都不打.)
    """
    assert set(MASK_RULES) == {"id_card", "bank_card", "phone", "email"}

    for name, mask in MASK_RULES.items():
        assert isinstance(mask, Mask)
        assert mask.name == name
        assert mask.pattern.pattern, f"{name} 没有形状"
