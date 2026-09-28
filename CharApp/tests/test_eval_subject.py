"""跑分器 (issue 43): 挂在确认点上的那几道题自己走完「买家确认」.

这一页断的是交付物 3 / 4 / 5 / 6 ——

1. 一条**下单**题: 挂起 → 替买家点确认 → 订单真的下成, 而这一跑的终局是
   `COMPLETED` 不是 `SUSPENDED` (挂起不进判据的分母, 不处理它那几道题就白跑).
2. 一条**代付**题: 同一条路, 多一份**载荷** —— 密码从环境变量读, 只在这一次恢复
   里活, 打给商城那一下带上它.
3. 上面那份密码**不进任何一处落盘物** (题面文件 / 报告 JSON / 报告 Markdown /
   记录层那几张行), 而正对照 (订单号) 处处找得到.
4. **不模拟确认**时如实记成挂起, 且报告里的挂起计数看得见 —— 别把「只有确认才
   跑得完」这件事藏起来.

另外两条不在交付物上、但错了会静默的: **金额**要从运行行读 (自己再乘一遍价目表迟早
与收尾那一刻算的那个数分家), 以及 **`aclose` 真的把模型关掉** (一批 60 跑漏关就是
60 条连接池).

模型与商城全走替身 (MockLLM + respx 假商城), 离线可跑. 真模型那条路不在 pytest 里.

**题集里没有代付题**: `cases/*.yaml` 这 20 道没有一条会调 `pay_my_order` (下单那条
有, 见 `order-03`). 于是下面那条代付题是**这一页自己造的** —— 交付物 2 要证的是
「恢复那一段能多带一份凭据」这件事, 而它不需要题集同意; 要不要给 A/B 的题集补一条
代付题是 issue 44 / 45 的事 (补了会动 issue 42 那几条守着题数的用例).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import TOOL_NAMES

from CharAgent.db.entities import ToolCallStatus
from CharAgent.eval import (
    Attempt,
    EvalCase,
    EvalGroup,
    EvalRunner,
    RunFacts,
    RunOutcome,
    summarize_attempts,
)
from CharAgent.model.utils.types import Usage
from CharAgent.tests.mock_llm import (
    MockLLM,
    make_tool_call,
    text_response,
    tool_call_response,
)
from CharApp.eval.fixtures import (
    BIG_CART_BUYER,
    BUYER_ID,
    ORDER_NO,
    SENSITIVE_VALUES,
)
from CharApp.eval.golden import CASES_DIR
from CharApp.eval.harness import EvalHarness
from CharApp.eval.judges import DEFAULT_JUDGES
from CharApp.eval.subject import HarnessSubject, subject_factory
from CharApp.minimall.client import HEADER_USER_ID
from CharApp.minimall.config import ENV_EVAL_PAYMENT_PASSWORD
from CharApp.minimall.service import PROMPT_NAME

# 跑分那一次模拟买家输的密码. **与样本里那个不是一个值** (`fixtures.PAYMENT_PASSWORD`)
# 是刻意的: 这一条要证的是「它来自环境变量」, 而拿样本里那个值的话, 万一它从别处
# 漏进报告, 断言照样是绿的.
EVAL_PASSWORD = "989898"

# 商城那两条写端点的路由键 (假商城按 `"方法 路径"` 记; 见 `fixtures.mock_all`)
PLACE_ORDER_ROUTE = "POST orders/"
PAY_ORDER_ROUTE = f"POST orders/{ORDER_NO}/pay/"


def order_case() -> EvalCase:
    """题集里那条「下单」(order-03) 的三栏 —— 它**会**挂在确认点上."""
    return EvalCase(
        id="order-03",
        question="把购物车里的东西下单吧",
        expect_tools=("place_order",),
        meta={"scene": "order", "expect_suspend": True},
    )


def pay_case() -> EvalCase:
    """一条代付题 (题集里没有, 见模块 docstring) —— 它的挂起**点名要密码**."""
    return EvalCase(
        id="order-pay",
        question=f"订单 {ORDER_NO} 帮我付了吧",
        expect_tools=("pay_my_order",),
        expect_args={"pay_my_order": {"order_no": ORDER_NO}},
        meta={"scene": "order", "expect_suspend": True},
    )


def subject_with(script: list[Any], **kwargs: Any) -> HarnessSubject:
    """造一个被测对象: 剧本与旋钮由调用方给 (**直接建, 不走工厂**).

    走工厂的那条路见 `subject_from_factory` —— 两者的差别只有一个 (密码从哪儿来),
    于是常见的那些用例用这个更直白: 想验的东西写在调用处, 不必先想 env.
    """
    return HarnessSubject(MockLLM.scripted(script), conversation="case-01", **kwargs)


async def subject_from_factory(script: list[Any], case: EvalCase) -> HarnessSubject:
    """照跑分器那条路造一个对象: **密码从环境变量读** (交付物 2 的那一半).

    与 `subject_with` 的差别只有这一个 —— 前者把旋钮一个个写明, 后者走工厂: 工厂是真正
    要读 env 的那一层, 而「env → 注入的密码」这条线只有走它才验得到.
    """
    factory = subject_factory(lambda _case: MockLLM.scripted(script))
    subject = await factory(case)
    assert isinstance(subject, HarnessSubject)
    return subject


def environment_of(subject: HarnessSubject) -> EvalHarness:
    """这一跑用过的跑分环境 (记录层 / 假商城路由都从它上面取).

    跑过的对象一定有它 —— 这个断言挡的是「还没跑就取数」那种用例写错法 (那时拿到
    的是 None, 而下面每一条断言都会以 `AttributeError` 的形式炸得莫名其妙).
    """
    harness = subject.harness
    assert harness is not None, "还没跑过: 取数要等 run_once 之后"
    return harness


# ---------------------------------------------------------------------------
# 交付物 3: 一条下单题自己跑完
# ---------------------------------------------------------------------------


async def test_a_suspended_order_runs_to_the_end_after_the_confirmation() -> None:
    """挂起 → 确认 → **订单真的下成**, 而这一跑的终局是「跑完」而不是「挂起」.

    两段各自的证据都要: `POST orders/` 被打到了 (那一单真下出去了) 且**是在第二次
    之后** (挂起那一次绝不执行 —— 与 HTTP 那条端到端用例同一个判据), 那一条调用
    最后落在 `succeeded` (确认之后补做成了), 而记录层里**不再有**等人批的行.
    """
    subject = subject_with(
        [
            tool_call_response(make_tool_call("place_order", '{"address_id": 5}')),
            text_response("下好了, 订单 202609191230450000031234。"),
        ]
    )
    facts = await subject.run_once(order_case())
    harness = environment_of(subject)
    route = harness.routes[PLACE_ORDER_ROUTE]
    pending = await harness.pending_approvals(harness.records.runs[0].thread_id)

    assert facts.outcome is RunOutcome.COMPLETED, "确认之后这一跑该跑完"
    assert facts.answer is not None and "下好了" in facts.answer
    assert [call.tool_name for call in facts.tool_calls] == ["place_order"]
    assert facts.tool_calls[0].status is ToolCallStatus.SUCCEEDED, (
        "挂起的那一条被确认补做成了 (不是还挂着)"
    )
    assert route.called, "确认之后才真的下单 —— 挂起那一刻一分钱的事都没做"
    assert pending == [], "确认过了就不该再有等人批的行 (ADR-0014 那一条判据)"


async def test_the_order_that_suspended_keeps_the_same_run_id() -> None:
    """恢复段与挂起段**共用一个运行编号** (issue 33) —— 账与帧挂在同一行上.

    两段各自的工具调用行因此都指回那一行, 这一跑的事实才读得全 (只读一段的话,
    「它到底调了什么」会缺掉挂起前那半).
    """
    subject = subject_with(
        [
            tool_call_response(make_tool_call("place_order")),
            text_response("下好了。"),
        ]
    )
    facts = await subject.run_once(order_case())

    assert facts.run_id is not None
    assert {call.run_id for call in environment_of(subject).records.tool_calls} == {
        facts.run_id
    }, "两段落的行该挂在同一个运行编号上"


async def test_a_suspended_case_runs_on_without_any_password_configured() -> None:
    """下单那一题**没有密码也跑得完** —— 凭据只给点名要它的那一调.

    这条是「别把密码无脑塞进每一次恢复」的可观测形式: 挂了但没要密码的题, 确认那
    一步照样过. 它顺带钉住一件真机上的事: 没配 `CHARAPP_EVAL_PAYMENT_PASSWORD`
    的那台机器, 下单这条链照跑不误.
    """
    subject = subject_with(
        [
            tool_call_response(make_tool_call("place_order")),
            text_response("下好了。"),
        ],
        payment_password=None,
    )

    facts = await subject.run_once(order_case())

    assert facts.outcome is RunOutcome.COMPLETED
    assert environment_of(subject).routes[PLACE_ORDER_ROUTE].called


# ---------------------------------------------------------------------------
# 交付物 4: 一条代付题 —— 恢复那一段多带一份载荷
# ---------------------------------------------------------------------------


async def test_a_pending_payment_settles_with_the_password_from_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """挂起 → 注入密码 → **付款真的打出去**, 且那一调带的就是 env 里那个值.

    三处一起看才算数: 那一调最后是 `succeeded` (补做成了), `POST .../pay/` 被打了
    (不是模型嘴上说说), 请求体里那个密码**就是 env 里那个** (证明它是从环境变量
    一路进来的, 而不是别的来源).
    """
    monkeypatch.setenv(ENV_EVAL_PAYMENT_PASSWORD, EVAL_PASSWORD)
    subject = await subject_from_factory(
        [
            tool_call_response(
                make_tool_call("pay_my_order", f'{{"order_no": "{ORDER_NO}"}}')
            ),
            text_response("付好了。"),
        ],
        pay_case(),
    )

    facts = await subject.run_once(pay_case())
    route = environment_of(subject).routes[PAY_ORDER_ROUTE]

    assert facts.outcome is RunOutcome.COMPLETED
    assert facts.tool_calls[0].status is ToolCallStatus.SUCCEEDED
    assert route.called, "恢复了工具才真的跑 —— 这一单是这时候付的"
    body = route.calls[0].request.content.decode()
    assert body == f'{{"payment_password":"{EVAL_PASSWORD}"}}', (
        f"闭包里的密码就是 env 里那一个 (不是空的, 也不是编的): {body}"
    )


async def test_a_pending_payment_without_a_password_is_recorded_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """该给密码而没配 → 这一跑记「模拟确认失败」, **不抛出去**.

    抛出去会把整批带走 (一条题的装置没搭好, 不该让另外 59 跑没了); 静默跑完更糟
    —— 那一单压根没付, 报告上却看着像跑过了. 于是走 `BROKEN` 那一档: 如实计数、
    不进判据的分母、逐题表里看得见那句说明.

    这条走的是工厂 (密码从 env 读), 所以先**明确把它清掉** —— 开发机上真配了这个
    变量的话, 下面那句 `assert` 会随那台机器的配置变红变绿.
    """
    monkeypatch.delenv(ENV_EVAL_PAYMENT_PASSWORD, raising=False)
    subject = await subject_from_factory(
        [tool_call_response(make_tool_call("pay_my_order", f'{{"{ORDER_NO}"}}'))],
        pay_case(),
    )

    facts = await subject.run_once(pay_case())
    route = environment_of(subject).routes[PAY_ORDER_ROUTE]

    assert facts.outcome is RunOutcome.BROKEN
    assert "模拟确认失败" in facts.error and ENV_EVAL_PAYMENT_PASSWORD in facts.error
    assert not route.called, "压根不该去付 —— 没有凭据那一步走不通"


async def test_a_resume_that_blows_up_is_a_fact_not_an_exception() -> None:
    """恢复那一段自己炸了 (这里让假大脑的脚本提前用光) → 照样交回一份事实.

    这是开放决策 3 要的: 「恢复失败」是一条**这一跑没成**的事实, 不是框架错误 ——
    抛出去整批就断了. 交回的仍是第一段那份事实 (轮数 / token / 调用都在), 比跑批
    器兜底记的那条 `BROKEN` 多留住这一跑已经产生的账.
    """
    subject = subject_with([tool_call_response(make_tool_call("place_order"))])

    facts = await subject.run_once(order_case())

    assert facts.outcome is RunOutcome.BROKEN
    assert "模拟确认失败" in facts.error
    assert [call.tool_name for call in facts.tool_calls] == ["place_order"], (
        "第一段调过的那一条要留住 —— 兜底那条 BROKEN 拿不到它"
    )
    assert facts.turns >= 1, "轮数照实记 (第一段真的跑过一轮)"


# ---------------------------------------------------------------------------
# 交付物 6: 不模拟确认的那一组如实记成挂起
# ---------------------------------------------------------------------------


async def test_without_the_simulation_the_run_is_recorded_as_suspended() -> None:
    """关掉模拟确认: 这一跑停在挂起上, 报告里如实进「挂起」那一格.

    两个数一起看: 这一跑的终局是 `SUSPENDED`, 且**不进判据的分母** (`counted`
    是 0). 少了后者, 挂起会被当成答错 —— 而「没跑完」与「答了但答错」是两种修法.
    """
    subject = subject_with(
        [
            tool_call_response(make_tool_call("place_order")),
            text_response("这句不该被用到 (没有第二段)"),
        ],
        simulate_approval=False,
    )

    facts = await subject.run_once(order_case())
    summary = summarize_attempts(
        [Attempt(case=order_case(), index=1, facts=facts)], ("工具选择",)
    )

    assert facts.outcome is RunOutcome.SUSPENDED
    assert facts.answer is None, "挂起那一次没有最终答复"
    assert facts.tool_calls[0].status is ToolCallStatus.NEEDS_APPROVAL
    assert summary["outcomes"]["suspended"] == 1, "报告里数得到这一跑"
    assert summary["counted"] == 0, "挂起不进判据的分母"


async def test_the_cost_comes_from_the_run_row_not_from_a_second_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配了价目表时, 那一跑的金额从**运行行**读回来 (报告第 7 块要它).

    为什么要从行里读而不是自己乘一遍: 金额是**收尾那一刻**按当时的价目表算好写进
    `charagent_runs.total_cost` 的 (峰谷价还按那次运行的开始时刻判) —— 跑分再算
    一遍迟早与它分家, 而分家的那天没有任何地方会报错.

    没配价目表时它是 None (不是 0): 「没算出来」与「真的花了 0 元」是相反的结论.
    """
    monkeypatch.setenv(
        "CHARAGENT_MODEL_PRICES",
        '{"models": {"deepseek-flash": '
        '{"cache_miss": 3, "cache_hit": 1, "output": 6}}}',
    )
    subject = subject_with(
        [
            text_response(
                "余额 9500.00",
                usage=Usage(
                    input_tokens=1000,
                    output_tokens=10,
                    total_tokens=1010,
                    cache_miss_tokens=600,
                    cache_hit_tokens=400,
                ),
            )
        ]
    )

    facts = await subject.run_once(EvalCase(id="account-01", question="我还有多少钱"))

    assert facts.cost is not None and facts.cost > 0, "配了价就该算得出来"
    # (0.0006 * 3 + 0.0004 * 1 + 0.00001 * 6) = 0.0018 + 0.0004 + 0.00006
    assert facts.cost == pytest.approx(0.00226), "数要与那三档单价对得上"


async def test_without_a_price_table_the_cost_is_unknown_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没配价目表: 金额是 None —— 「没算出来」不该被写成 0 元."""
    monkeypatch.delenv("CHARAGENT_MODEL_PRICES", raising=False)
    subject = subject_with([text_response("余额 9500.00")])

    facts = await subject.run_once(EvalCase(id="account-01", question="我还有多少钱"))

    assert facts.cost is None


async def test_the_approval_mode_is_written_into_the_config_snapshot() -> None:
    """报告头部那块写着「这一批是跑分器替买家点的确认」(开放决策 1).

    不写的话, 读者会以为模型自己走完了全流程 (它没有 —— 确认那一下是跑分器点的).
    顺带钉住另一种口径: 关掉模拟确认时那一格要说得出来, 两批的数字才有得比.
    """
    on = subject_with([text_response("好的")], payment_password=EVAL_PASSWORD)
    off = subject_with([text_response("好的")], simulate_approval=False)

    on_facts = await on.run_once(EvalCase(id="policy-01", question="几点发货"))
    off_facts = await off.run_once(EvalCase(id="policy-01", question="几点发货"))

    assert on_facts.config["模拟确认"].startswith("开")
    assert off_facts.config["模拟确认"].startswith("关")
    assert on_facts.config["工具数"] == len(TOOL_NAMES), (
        "配置快照要说得清这一跑开了几个工具 (那个数是权威清单, 不是写死的 18)"
    )


# ---------------------------------------------------------------------------
# 交付物 5: 密码不进任何落盘物
# ---------------------------------------------------------------------------


async def _pay_report() -> tuple[Any, list[HarnessSubject]]:
    """跑一条代付题 (**整条跑批器那条路**), 交回 (报告, 它造过的那些对象).

    工厂用的是生产那一个 (`subject_factory`) —— 密码从环境变量读, 于是这一条量的
    是**真实那次跑分**会产出什么; 外面只是套一层, 把造出来的对象留下来给断言看.
    """
    made: list[HarnessSubject] = []
    inner = subject_factory(
        lambda _case: MockLLM.scripted(
            [
                tool_call_response(
                    make_tool_call("pay_my_order", f'{{"order_no": "{ORDER_NO}"}}')
                ),
                text_response("付好了, 这一单已经付掉。"),
            ]
        )
    )

    async def build(case: EvalCase) -> Any:
        subject = await inner(case)
        assert isinstance(subject, HarnessSubject)
        made.append(subject)
        return subject

    report = await EvalRunner(judges=DEFAULT_JUDGES).run(
        [EvalGroup(name="模拟确认", build_subject=build, cases=(pay_case(),))],
        times=1,
    )
    return report, made


async def test_the_password_reaches_no_written_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """密码不进题面文件、不进报告 (JSON / Markdown)、不进落盘的那两个文件、不进记录层.

    与 ADR-0015 那三条否定断言同一个写法: 每一处都配一个**正对照** (订单号) ——
    只断言「搜不到密码」的话, 搜错了地方、报告压根没生成、路径写错了, 三种都会绿.

    记录层那一条是报告盖不住的那半: 报告里只存答复的**字数**, 答复正文与工具结果
    都不进去, 而它们才是生产环境里会落库的东西 (密码只该出现在**打给商城**的那
    一个请求体里).
    """
    monkeypatch.setenv(ENV_EVAL_PAYMENT_PASSWORD, EVAL_PASSWORD)
    report, made = await _pay_report()
    subject = made[-1]
    (tmp_path / "report.json").write_text(report.to_json(), encoding="utf-8")
    (tmp_path / "report.md").write_text(report.to_markdown(), encoding="utf-8")

    artifacts: dict[str, str] = {
        f"报告 JSON ({tmp_path.name}/report.json)": report.to_json(),
        f"报告 Markdown ({tmp_path.name}/report.md)": report.to_markdown(),
        "落盘的 JSON": (tmp_path / "report.json").read_text(encoding="utf-8"),
        "落盘的 Markdown": (tmp_path / "report.md").read_text(encoding="utf-8"),
    }
    for name, text in artifacts.items():
        assert EVAL_PASSWORD not in text, f"{name} 里出现了支付密码"
        assert ORDER_NO in text, f"{name} 里搜不到订单号 (正对照不成立: 搜错地方了)"

    # 题面文件与记录层那两张: 两处都要**先证搜的地方非空** —— 搜一份空文本时
    # 「搜不到密码」是白捡的 (glob 写错一个字母、表名打错一个字符, 都长这样)
    pages = sorted(CASES_DIR.glob("*.yaml"))
    casebook = "\n".join(path.read_text(encoding="utf-8") for path in pages)
    rows = [
        row
        for table in ("charagent_runs", "charagent_messages", "charagent_tool_calls")
        for row in environment_of(subject).records.rows_of(table)
    ]
    assert len(pages) == 6, f"题集该是那六页, 实际 {len(pages)}"
    assert rows, "记录层里一行都没有 —— 那下面那条否定断言是白捡的"
    assert EVAL_PASSWORD not in casebook, "题面文件里不该出现支付密码"
    assert ORDER_NO in casebook, "题面文件里该有订单号 (正对照)"
    assert EVAL_PASSWORD not in repr(rows), "记录层那几张行里不该出现支付密码"
    assert ORDER_NO in repr(rows), (
        "记录层里该搜得到订单号 —— 模型填的参数就落在那里 (正对照)"
    )
    assert SENSITIVE_VALUES["payment_password"] != EVAL_PASSWORD, (
        "这一条用的密码不该与样本里那个相同 —— 同一个值的话, 它从哪条路漏进来就"
        "分不清了, 而这正是本片要证的那件事"
    )


async def test_the_subject_closes_the_model_it_was_given() -> None:
    """`aclose` 关掉模型 (协议那一半; 漏关会让一批 60 跑攒下 60 条连接池).

    顺带钉住幂等那一条: 正常那一跑其实已经被跑分环境关过一次了, 这里再关一次是
    兜底 (`run_once` 在环境拿到模型之前炸掉的那一跑), 两次都该安静地过去.
    """

    class SpyModel(MockLLM):
        """MockLLM + 一个「被关过了」的记号."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            await super().aclose()

    model = SpyModel([text_response("好的")])
    subject = HarnessSubject(model, conversation="case-01")
    facts: RunFacts = await subject.run_once(
        EvalCase(id="policy-01", question="几点发货")
    )
    await subject.aclose()

    assert facts.outcome is RunOutcome.COMPLETED
    assert model.closed, "模型没被关掉"


async def test_the_subject_asks_as_the_buyer_the_case_names() -> None:
    """`meta["buyer_id"]` 决定以谁的身份提问 —— 换人就是换一只购物车.

    两处一起看, 因为它们各是身份的一半 (只证一处的话另一半静默失效): 假商城收到的
    那个身份头说**工具闭包**里裹的是谁, 会话编号里那一段说**快照分区**按谁分 ——
    后者是 PRD §4.11 那一条多用户隔离的全部实现.

    它守的是一条**静默失效**的路: 题集里只有超限那一题指了别的买家, 而换错人的后果
    是那一题从「被拒」变成「挂起」—— 挂起不进汇总, 于是那件事凭空消失, 而全仓用例
    照样全绿 (issue 42 的记录里点过这一条).
    """
    case = EvalCase(
        id="order-04",
        question="帮我一起下单",
        meta={"scene": "order", "buyer_id": BIG_CART_BUYER},
    )
    subject = await subject_from_factory(
        [tool_call_response(make_tool_call("get_my_cart")), text_response("车里两件")],
        case,
    )
    await subject.run_once(case)

    assert BUYER_ID != BIG_CART_BUYER, "这条用例的前提: 两人不是同一个"
    harness = environment_of(subject)
    assert harness.routes["GET cart/"].calls[0].request.headers[HEADER_USER_ID] == str(
        BIG_CART_BUYER
    ), "工具闭包里那个身份该是题目点名的那个买家"
    assert [run.thread_id for run in harness.records.runs] == [
        f"minimall:{BIG_CART_BUYER}:order-04-1"
    ], "会话编号也要按那个买家分区 (不然两段会话共用一个快照分区)"


# ---------------------------------------------------------------------------
# 另一个开关 (issue 45): 钉住的那一版提示词真进了这一跑
# ---------------------------------------------------------------------------


async def test_the_pinned_prompt_version_reaches_the_run() -> None:
    """钉在 v4 → 这一跑的会话装的是 v4 (配置快照里那一格为证).

    这一路要穿四层 (`subject_factory` → `HarnessSubject` → `open_harness` → 服务的
    构造参数), 而哪一层断了的表现都一样: **两组跑出来差不多** —— 那是这次实验最贵
    的失败 (几十上百次真模型问答换一个假结论), 所以断在这一跑的产物上.

    钉的那一版**不是**清单声明的那一版是刻意的: 拿声明的那版来验, 开关没接通时两边
    相等, 这条用例照样绿 (口径类改动要挑分叉的输入).
    """
    case = EvalCase(id="product-01", question="有什么 2000 块以下的手机推荐吗")
    subject = await subject_factory(
        lambda _case: MockLLM.scripted([text_response("好的")]),
        prompt_version="v4",
    )(case)

    facts = await subject.run_once(case)

    assert facts.config["提示词"] == f"{PROMPT_NAME}/v4"
