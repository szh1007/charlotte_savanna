"""跑分样本与敏感值清单 (issue 41): 那份清单与日志名单对不对得上.

清单 (`eval/fixtures.SENSITIVE_VALUES`) 是回答合规判据 (issue 42 / 45 的 v4) 的
搜索词 —— 它一旦与样本或与日志名单漂开, 判据就会**静默失效**: 搜不到原文 = 判成
合规, 而没有任何地方会报警. 这一页就是那几道防漂的断言.

同型的先例在 `test_log_redaction.py` 的最后一条 (名单里的名字 vs 样本载荷的名字):
两份独立抄件互相印证, 抄错一个名字当场红.
"""

from __future__ import annotations

from CharApp.eval.fixtures import (
    ADDRESSES,
    ORDER_NO,
    PAID_ORDER,
    PAYMENT_PASSWORD,
    PROFILE,
    SENSITIVE_VALUES,
)
from CharApp.minimall.log_redaction import LOG_FIELDS


def test_the_sensitive_list_covers_every_name_the_log_list_masks() -> None:
    """清单的名字 = 日志名单的名字 **减去余额**.

    两张表**不是**同一张, 差的就是这一格: 余额在日志里要打码 (留痕不该带着买家的
    钱), 而回答里**必须**说得出来 —— 看余额正是助手被设计来做的事, 四个既有用例
    要求它复述. 其余名字必须一一对上: 一边加了字段而另一边忘了, 就是一次静默失效.
    """
    masked = {path.rsplit(".", 1)[-1] for path in LOG_FIELDS}

    assert set(SENSITIVE_VALUES) == masked - {"balance"}
    assert "payment_password" in SENSITIVE_VALUES, "支付密码那条永远要在 (ADR-0015)"


def test_every_value_is_derived_from_the_samples() -> None:
    """每个真值都取自样本里那一份 (清单是从样本派生的, 不是另抄的一份).

    逐个比是**故意的**: 这里比的是「清单拿的是样本的值」, 而不是「清单里有个看着
    像手机号的东西」—— 样本换了而清单没跟着换, 这条当场红.
    """
    assert SENSITIVE_VALUES["phone"] == PROFILE["phone"]
    assert SENSITIVE_VALUES["email"] == PROFILE["email"]
    assert SENSITIVE_VALUES["receiver_name"] == ADDRESSES[0]["receiver_name"]
    assert SENSITIVE_VALUES["detail"] == ADDRESSES[0]["detail"]
    assert SENSITIVE_VALUES["payment_password"] == PAYMENT_PASSWORD


def test_the_balance_and_the_order_number_stay_out() -> None:
    """刻意排除的两样: 余额 (样本里两处) 与订单号.

    余额在样本里有**两处** (账户的 `balance` 与付款回执的 `balance_remaining`),
    两处都得排 —— 只排一处的话, 代付那道题会被自己的判据判成违规 (答复里必然带着
    「付完还剩多少」). 订单号同理: 排查订单要看的正是它, 已有两条用例守着它活着.
    """
    values = set(SENSITIVE_VALUES.values())

    assert PROFILE["balance"] not in values
    assert PAID_ORDER["balance_remaining"] not in values
    assert ORDER_NO not in values
