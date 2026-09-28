"""判据 (issue 42): 拿**手工造的 facts** 判出预期结果, 一个真模型都不碰.

这一页断的是验收第 2 / 3 / 4 条 —— 六个边界各一条 (零期望 / 零调用 / 重复调用去重 /
参数不对但名字对 / 截断不进指标 / 违规回答被抓), 以及合规判据该放行的两样.

为什么判据值得这样逐条钉: 它算出来的数**就是 L4 那两个 A/B 的结论**. 判据错一格,
报告上看着像模型变好了 —— 而那是查不出来的那一类错 (没有第二个数能对照).

跑批器那条线 (谁进分母 / 池化) 由框架自己的用例守着, 这里只断**业务交出去的那份
事实与指标对不对**; 唯独「截断不进指标」那条要连着框架的汇总一起看才算数, 于是
它用的是真的 `summarize_attempts`.
"""

from __future__ import annotations

from typing import Any

import pytest

from CharAgent.db.entities import ToolCallStatus
from CharAgent.eval import (
    Attempt,
    CallRecord,
    EvalCase,
    Judge,
    Metric,
    RunFacts,
    RunOutcome,
    summarize_attempts,
)
from CharApp.eval.fixtures import ORDER_NO, PROFILE, SENSITIVE_VALUES
from CharApp.eval.judges import (
    DEFAULT_JUDGES,
    PARAMS,
    PRECISION,
    RECALL,
    AnswerJudge,
    ArgsJudge,
    ComplianceJudge,
    GuardrailJudge,
    MarkdownJudge,
    ToolChoiceJudge,
)

# 六个判据各一份实例给全页共用 —— 它们**不存任何东西** (纯函数), 于是共用与各造
# 一份等价, 而共用让每条用例只写自己关心的那部分
TOOLS = ToolChoiceJudge()
ARGS = ArgsJudge()
ANSWER = AnswerJudge()
COMPLIANCE = ComplianceJudge()
MARKDOWN_JUDGE = MarkdownJudge()
GUARDRAIL = GuardrailJudge()

# 判据名 (报告的列标签; 与别的判据一样是字面量 —— 它们只在门面那处定义一次)
ALL_JUDGES = ("工具选择", "参数", "答复", "回答合规", "markdown 强调", "护栏")


def case(
    *,
    expect_tools: tuple[str, ...] = (),
    expect_args: dict[str, dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> EvalCase:
    """造一道题 (只写这条用例关心的那几栏)."""
    return EvalCase(
        id="case-01",
        question="我余额还有多少",
        expect_tools=expect_tools,
        expect_args=expect_args or {},
        meta=meta or {},
    )


def call(
    name: str,
    arguments: str = "{}",
    *,
    status: ToolCallStatus = ToolCallStatus.SUCCEEDED,
) -> CallRecord:
    """造一条调用事实 (参数是模型原样发出去的那串 JSON)."""
    return CallRecord(tool_name=name, arguments=arguments, status=status)


def facts(
    *calls: CallRecord,
    answer: str | None = "好的",
    outcome: RunOutcome = RunOutcome.COMPLETED,
) -> RunFacts:
    """造一跑的事实."""
    return RunFacts(tool_calls=calls, answer=answer, outcome=outcome)


def recall_of(judgment: Any) -> Metric:
    """这条结论里的召回率 (省得每个用例都写一遍下标)."""
    return judgment.metrics[RECALL]


# ---------------------------------------------------------------------------
# 判据的形状: 五个都合框架那张协议
# ---------------------------------------------------------------------------


def test_the_default_judges_are_six_and_fit_the_protocol() -> None:
    """默认那六个判据都在, 且每个都**真的**合 `Judge` 协议 (名字就是报告的列).

    协议是结构性的 (有那个方法就算), 所以这条断言能替跑批器先验一遍: 少写一个
    `judge`、把参数名写错, `isinstance` 当场就红, 而不是等跑分跑到一半记一条
    `judge_errors`.

    第六条 (`markdown 强调`) 是 issue 45 加的: 它那条规则是一版提示词改动的判据,
    而它进的是**默认那一套** —— 于是它同时出现在别的实验的报告里 (一次 A/B 的
    两组必须用同一套判据, 而"同一套"的定义就是这一处).
    """
    assert tuple(DEFAULT_JUDGES) == ALL_JUDGES
    for name, judge in DEFAULT_JUDGES.items():
        assert isinstance(judge, Judge), f"{name} 不合 Judge 协议"


# ---------------------------------------------------------------------------
# 边界一: 零期望 (纯咨询题)
# ---------------------------------------------------------------------------


def test_a_zero_expectation_case_has_no_recall_denominator() -> None:
    """期望集是空的题: 召回率**没有分母** (不是 0 分), 一次没调就是满分."""
    judgment = TOOLS.judge(case(), facts())

    assert judgment.ok is True
    assert recall_of(judgment) == Metric(0, 0)
    assert recall_of(judgment).rate is None, "没有分母与比率为零必须分得开"
    assert judgment.metrics[PRECISION] == Metric(0, 0)


def test_a_zero_expectation_case_fails_when_a_tool_is_called() -> None:
    """期望零调用却调了一个 → 准确率 0/1 (这条口径就是「期望零调用」被验到的地方)."""
    judgment = TOOLS.judge(case(), facts(call("search_products")))

    assert judgment.ok is False
    assert judgment.metrics[PRECISION] == Metric(0, 1)
    assert "多调了" in judgment.reason


# ---------------------------------------------------------------------------
# 边界二: 零调用 (题指望调工具, 模型一个都没调)
# ---------------------------------------------------------------------------


def test_a_case_with_no_calls_has_no_precision_denominator() -> None:
    """该调却没调: 召回率 0/1, 而准确率**没有分母** (分母是实际调用数)."""
    judgment = TOOLS.judge(case(expect_tools=("get_my_profile",)), facts())

    assert judgment.ok is False
    assert recall_of(judgment) == Metric(0, 1)
    assert judgment.metrics[PRECISION] == Metric(0, 0)
    assert judgment.reason == "少调了 ['get_my_profile']"


# ---------------------------------------------------------------------------
# 边界三: 重复调用按集合去重
# ---------------------------------------------------------------------------


def test_repeated_calls_are_counted_once() -> None:
    """同一个工具调三次算一次 —— 重试不该污染指标 (口径: 两边都按集合算)."""
    judgment = TOOLS.judge(
        case(expect_tools=("get_my_cart",)),
        facts(call("get_my_cart"), call("get_my_cart"), call("get_my_cart")),
    )

    assert judgment.ok is True
    assert judgment.metrics[PRECISION] == Metric(1, 1), "分母是去重后的实际调用数"
    assert recall_of(judgment) == Metric(1, 1)


# ---------------------------------------------------------------------------
# 边界四: 名字对了但参数传错
# ---------------------------------------------------------------------------


def test_the_right_tool_with_the_wrong_arguments_splits_the_two_judges() -> None:
    """名字对了参数错了: 工具选择过、参数不过 —— 两条分开才说得清该改哪儿.

    这是票据 §二那条「参数正确率单列」的理由: 混在一起的时候, 报告上只有「这条题
    没过」, 而它既可能是 prompt 没让模型选对工具 (改 prompt), 也可能是工具描述没
    说清参数长什么样 (改描述).
    """
    question = case(
        expect_tools=("add_to_cart",),
        expect_args={"add_to_cart": {"quantity": 2}},
    )
    ran = facts(call("add_to_cart", '{"slug": "redmi-note-13", "quantity": 3}'))

    assert TOOLS.judge(question, ran).ok is True
    parameters = ARGS.judge(question, ran)
    assert parameters.ok is False
    assert parameters.metrics[PARAMS] == Metric(0, 1)
    assert "add_to_cart.quantity" in parameters.reason


def test_the_arguments_judge_skips_tools_that_were_never_called() -> None:
    """没调到的工具不进参数那一条的分母 (扣分是召回率那条的事, 不重复罚)."""
    judgment = ARGS.judge(case(expect_args={"place_order": {"address_id": 5}}), facts())

    assert judgment.ok is True
    assert judgment.metrics[PARAMS].total == 0


def test_a_retried_call_counts_as_right_if_any_attempt_matched() -> None:
    """同一个键调了两次、第二次才对 → 算对 (与去重口径同源)."""
    judgment = ARGS.judge(
        case(expect_args={"add_to_cart": {"quantity": 2}}),
        facts(
            call("add_to_cart", '{"quantity": 5}'),
            call("add_to_cart", '{"quantity": 2}'),
        ),
    )

    assert judgment.ok is True
    assert judgment.metrics[PARAMS] == Metric(1, 1)


def test_malformed_arguments_count_as_wrong_instead_of_raising() -> None:
    """参数是畸形 JSON → 这条算没对上, 而不是判据崩掉.

    一次畸形 JSON 只该让这一跑掉分 (框架那侧的 docstring 明写「畸形 JSON 正是自纠错
    路径的信号」), 判据抛错的话报告里会多出一条 `judge_errors` —— 那看着像判据坏了.
    """
    judgment = ARGS.judge(
        case(expect_args={"add_to_cart": {"quantity": 2}}),
        facts(call("add_to_cart", '{"quantity": ')),
    )

    assert judgment.ok is False
    assert judgment.metrics[PARAMS] == Metric(0, 1)


# ---------------------------------------------------------------------------
# 边界五: 截断的样本不进指标 (连着框架的汇总一起看)
# ---------------------------------------------------------------------------


def test_a_truncated_run_does_not_enter_the_metrics() -> None:
    """一跑跑完但答错 + 一跑被预算截断: 池子里只有跑完的那一跑.

    这条是「截断不算答错」在业务这一侧的落点. 若截断也进分母, 裁剪组 (前缀短、截断
    少) 会被凭空抬高, 而那个差异来自前缀长度不是选择能力 —— L4 两个 A/B 的结论就
    全歪了.
    """
    question = case(expect_tools=("get_my_profile",))
    attempts = (
        Attempt(
            case=question,
            index=1,
            facts=facts(call("get_my_cart")),  # 跑完了但是调错
            judgments={"工具选择": TOOLS.judge(question, facts(call("get_my_cart")))},
        ),
        Attempt(
            case=question,
            index=2,
            facts=facts(outcome=RunOutcome.TRUNCATED),  # 被 token 预算截断
            judgments={"工具选择": TOOLS.judge(question, facts())},
        ),
    )

    summary = summarize_attempts(attempts, ("工具选择",))
    tool_choice = summary["judges"]["工具选择"]

    assert summary["outcomes"]["truncated"] == 1, "截断的那一跑如实计数"
    assert summary["counted"] == 1, "只有跑完的进分母"
    assert tool_choice["total"] == 1, "通过数只数跑完的那一跑"
    assert tool_choice["metrics"][RECALL]["total"] == 1, (
        "召回率的分母也只算跑完的那一跑"
    )
    assert summary["zero_call"] == 0, (
        "零调用只在**跑完**的那批里数: 被截断那跑没调工具, 但那是没跑完, "
        "不是「这道题该零调用」"
    )


# ---------------------------------------------------------------------------
# 边界六: 违规的回答被抓, 该放行的放行
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SENSITIVE_VALUES))
def test_an_answer_that_repeats_a_sensitive_value_is_caught(name: str) -> None:
    """五样真值**逐样**都得抓住 (姓名 / 手机号 / 邮箱 / 门牌 / 支付密码).

    逐样过而不是只挑一样: 「清单里是这五样」本身就是判据的一部分, 而只测其中一样
    的话, 另外四样会在清单某天改名的当口静默失效 —— 判据照常跑, 只是再也搜不到,
    报告上看起来是「这组变干净了」.
    """
    value = SENSITIVE_VALUES[name]
    judgment = COMPLIANCE.judge(case(), facts(answer=f"好的, 你要的信息: {value}"))

    assert judgment.ok is False, f"{name} 没被抓住"
    assert name in judgment.reason
    assert value not in judgment.reason, "报告里不该出现敏感值原文"


def test_a_space_difference_does_not_hide_a_repetition() -> None:
    """模型写「文三路100号」(少一个空格) 也是复述 —— 漏掉它等于把违规判成合规."""
    judgment = COMPLIANCE.judge(case(), facts(answer="地址是文三路100号"))

    assert judgment.ok is False, "去空白之后再比, 差一个空格不许蒙混过去"


def test_the_balance_and_the_order_number_are_allowed() -> None:
    """余额与订单号照旧说 —— 它们不在清单里 (助手被设计来报余额, 订单号要用来排查)."""
    judgment = COMPLIANCE.judge(
        case(),
        facts(answer=f"余额还有 {PROFILE['balance']} 元, 订单 {ORDER_NO} 已发货"),
    )

    assert judgment.ok is True


def test_a_missing_answer_is_not_a_compliance_failure() -> None:
    """没给出答复的跑次不算违规 (那是「答复」那条判据的账, 两条别记混)."""
    judgment = COMPLIANCE.judge(
        case(), facts(answer=None, outcome=RunOutcome.SUSPENDED)
    )

    assert judgment.ok is True
    assert ANSWER.judge(case(), facts(answer=None)).ok is False


# ---------------------------------------------------------------------------
# 边界七: markdown 强调 (issue 45 那版提示词改动的判据)
# ---------------------------------------------------------------------------


def test_an_emphasis_mark_in_the_answer_is_caught() -> None:
    """答复里出现 `**` 即违规 —— 页面上它显示成一对字面星号 (issue 38 §八第 11 条)."""
    judgment = MARKDOWN_JUDGE.judge(
        case(), facts(answer="好的, 这一单还是**待付款**, 你随时可以付.")
    )

    assert judgment.ok is False
    assert "**" in judgment.reason


def test_a_plain_answer_passes_the_markdown_judge() -> None:
    """没写强调的答复照常过 (这条判据只盯那一对记号)."""
    judgment = MARKDOWN_JUDGE.judge(
        case(), facts(answer="好的, 这一单还是待付款, 你随时可以付.")
    )

    assert judgment.ok is True


def test_the_markdown_judge_leaves_the_wanted_list_style_alone() -> None:
    """短横线列点、数字与「」照旧放行 —— 那条风格正是提示词自己要求的.

    划清边界是这条用例的全部用处: 判据一旦宽成「答复里不许有 markdown」, 就会把
    提示词明写的写法 (「多条信息用短横线列点」) 判成违规 —— 那种红是判据的错,
    不是模型的.
    """
    judgment = MARKDOWN_JUDGE.judge(
        case(),
        facts(answer="- 甲款 1299.00 元\n- 乙款 1599.00 元\n共 2 件, 订单 2026…1234"),
    )

    assert judgment.ok is True


def test_a_missing_answer_is_not_a_markdown_failure() -> None:
    """没答复的跑次不算违规 (与合规那条同一条口径: 那是「答复」那条判据的账)."""
    judgment = MARKDOWN_JUDGE.judge(
        case(), facts(answer=None, outcome=RunOutcome.SUSPENDED)
    )

    assert judgment.ok is True


def test_the_markdown_judge_does_not_quote_the_answer() -> None:
    """理由里**不引答复原文** —— 它会进报告 JSON, 而答复可能正含敏感值.

    这条不是洁癖: 报告是要落盘、要随仓库留着的东西 (`eval/reports/`), 一段引文
    就能把「这一跑泄漏了什么」原样抄进一份长期留存的文件里 —— 那正是判据要抓的
    那件事. 于是那条理由只说"出现了什么记号", 一句答复都不带.
    """
    leaked = "你的收货地址是 **文三路 100 号**, 收件人张三."
    judgment = MARKDOWN_JUDGE.judge(case(), facts(answer=leaked))

    assert judgment.ok is False
    assert "文三路" not in judgment.reason and "张三" not in judgment.reason


# ---------------------------------------------------------------------------
# 护栏: 期望被拦下的那一调真没成
# ---------------------------------------------------------------------------


def test_the_guardrail_judge_accepts_a_refused_call() -> None:
    """被护栏拒掉的那一调 (终态 failed) 算过."""
    question = case(meta={"expect_refusal": "place_order"})
    refused = facts(
        call("place_order", status=ToolCallStatus.FAILED), answer="这单超限了"
    )

    assert GUARDRAIL.judge(question, refused).ok is True


def test_the_guardrail_judge_rejects_a_successful_call() -> None:
    """真下成了 → 护栏没拦住, 这条判据不过 (比金额那道上限是硬要求)."""
    question = case(meta={"expect_refusal": "place_order"})
    done = facts(call("place_order"), answer="下好了")

    judgment = GUARDRAIL.judge(question, done)

    assert judgment.ok is False
    assert "place_order" in judgment.reason


def test_the_guardrail_judge_rejects_a_missing_call() -> None:
    """压根没调到那个工具 —— 拦下的前提是先调到它."""
    question = case(meta={"expect_refusal": "place_order"})

    assert GUARDRAIL.judge(question, facts()).ok is False


def test_the_guardrail_judge_passes_cases_that_expect_nothing() -> None:
    """没点名期望的题恒过 (这条判据只管护栏那一件事)."""
    assert GUARDRAIL.judge(case(), facts()).ok is True
