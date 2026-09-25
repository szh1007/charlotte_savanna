"""成本口径的自检 (不需要数据库): 用量 + 开始时刻 -> 金额, 以及「算不出来」的来路.

这一批用例守的是四条口径:

1. **三档各乘各的价** —— 命中输入 / 未命中输入 / 输出 各有各的单价. 拿总量乘一个
   均价是错的 (真实价目表里这三档差着量级). 测试里那张表刻意让三档互不相同、
   数字好认 —— 谁被乘成了谁, 断言里立刻露馅.
2. **推理分量不重复计** —— `reasoning_tokens` 是 `output_tokens` 的**明细**
   (`usage.completion_tokens_details` 这个层级就是这么分的), 已经含在输出里了.
   再单乘一遍会让金额系统性偏高, 而**偏高与偏低都不会报错** (ticket 28).
3. **峰谷看运行开始那一刻, 且工作日是中国日历说了算** —— 用例里的日期全是真数据:
   `2026-09-25` 是**周五但休息日** (拿「周一到周五」硬判就会算错峰谷), `2026-09-20`
   是**周日但调休上班**. 这两条正是「为什么必须用日历库」的证据.
4. **算不出来就说算不出来** —— 缺单价 / 缺分量 / 日历过期一律给原因, 绝不给 0:
   「报 0 比报不出来更糟, 因为 0 看起来像个答案」(ADR-0005 那条纪律).

单价一律按**每百万 token**给, 金额量化到 6 位小数 —— 与 `total_cost` 那一列的
`Numeric(14, 6)` 同标度.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from CharAgent.db.cost import (
    ENV_MODEL_PRICES,
    CostGap,
    PeakRule,
    PriceTable,
    RunCost,
    cost_of,
    ensure_pricing_ready,
)
from CharAgent.db.errors import DataConfigError, PricingNotReadyError

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 一份**全天一价**的价目表 (三档写法): 未命中 2 / 命中 0.5 / 输出 8, 每百万 token
FLAT_JSON = '{"models": {"m": {"cache_miss": 2, "cache_hit": 0.5, "output": 8}}}'

# 一份**峰谷两套**的价目表: 峰 2 / 0.04 / 8, 谷 1 / 0.02 / 4 (每百万 token).
# 数字取的是 deepseek-flash 已发布价那一对, 于是「两套差一倍」这个量级感是真的
SPLIT_JSON = json.dumps(
    {
        "timezone": "Asia/Shanghai",
        "peak_windows": [["09:00", "12:00"], ["14:00", "18:00"]],
        "models": {
            "m": {
                "peak": {"cache_miss": 2, "cache_hit": 0.04, "output": 8},
                "valley": {"cache_miss": 1, "cache_hit": 0.02, "output": 4},
            }
        },
    }
)


def bj(text: str) -> datetime:
    """北京时间 -> 带时区的时刻 (库里存的是 UTC, 这是人读的那一侧).

    用例一律用北京时间写字: 峰谷规则是供应商按它自己所在地的作息定的, 而转换那一步
    正是被测代码的一部分 (写 UTC 反而把要验的东西绕过去了).
    """
    return datetime.fromisoformat(text).replace(tzinfo=SHANGHAI)


def cost(
    cache_miss: int | None,
    cache_hit: int | None,
    output: int | None,
    *,
    input_tokens: int | None = None,
    moment: datetime | None = bj("2026-09-21 10:00"),  # 周一 10:00 = 峰
    table: PriceTable | None = None,
    model: str | None = "m",
) -> RunCost:
    """按一次运行的用量折算 (顺序与 runs 那几列一致; 默认给峰时段的周一开始时刻)."""
    return cost_of(
        model=model,
        table=table if table is not None else PriceTable.from_json(FLAT_JSON),
        moment=moment,
        input_tokens=input_tokens,
        cache_miss_tokens=cache_miss,
        cache_hit_tokens=cache_hit,
        output_tokens=output,
    )


def flat() -> PriceTable:
    """全天一价那张表."""
    return PriceTable.from_json(FLAT_JSON)


def split() -> PriceTable:
    """峰谷两套那张表."""
    return PriceTable.from_json(SPLIT_JSON)


# ---------------------------------------------------------------------------
# 三档分别乘
# ---------------------------------------------------------------------------


def test_three_tiers_are_multiplied_separately():
    """三档各乘各的价, 合计是三个金额之和 (拿总量乘一个均价是错的).

    三个数刻意取到百万级, 于是每条金额都是一个好认的整数 —— 乘错档位立刻红.
    """
    result = cost(1_000_000, 2_000_000, 500_000)

    assert result.known
    assert result.line_of["cache_miss"].amount == Decimal("2"), "100 万未命中 x 2/M"
    assert result.line_of["cache_hit"].amount == Decimal("1"), "200 万命中 x 0.5/M"
    assert result.line_of["output"].amount == Decimal("4"), "50 万输出 x 8/M"
    assert result.total == Decimal("7")
    assert result.tier is None, "全天一价的模型不分峰谷"


def test_the_same_input_split_differently_costs_differently():
    """同样多的输入 token, 命中与未命中的比例不同 -> 金额不同.

    这是「命中与未命中必须分开算」那条的**分叉输入**: 两次输入总量完全一样
    (1000), 只有拆分不同. 若谁把两档合起来按一个价算, 这两个结果会相等.
    """
    all_hit = cost(0, 1_000, 0)
    all_miss = cost(1_000, 0, 0)

    assert all_hit.total == Decimal("0.0005"), "1000 命中 x 0.5/M"
    assert all_miss.total == Decimal("0.002"), "1000 未命中 x 2/M"
    assert all_hit.total != all_miss.total


def test_price_unit_is_per_million_tokens():
    """单价的单位是**每百万 token** (与各家价目表同形, 不必自己换算)."""
    assert cost(1_000_000, 0, 0).total == Decimal("2")


def test_amount_keeps_six_decimals_like_the_column():
    """金额量化到 6 位小数 —— 与 `total_cost` 那一列同标度.

    单个 token 的金额落在小数第 6 位以外, 四舍五入后是 0.000002 (而不是 0):
    这是**真的算出来了**, 只是小. 与「算不出来」是两回事 (后者不给金额).
    """
    result = cost(1, 1, 1)

    assert result.line_of["cache_miss"].amount == Decimal("0.000002")
    assert result.line_of["cache_hit"].amount == Decimal("0.000001")
    assert result.line_of["output"].amount == Decimal("0.000008")
    assert result.total == Decimal("0.000011")


def test_an_amount_below_the_column_scale_is_not_written_as_zero():
    """算出来的钱小到这一列存不下 (小于 1e-6) -> 报「存不下」, **不写 0**.

    0 在这一列里的意思是「真的没花钱」. 三个命中 token 值 6e-8 元 —— 现实中不会
    出现这么便宜的一趟, 但「算不出来」与「算出来是零」必须分得开: 写 0 就是撒谎.
    """
    # 4 个命中 token x 0.04/M = 1.6e-7 元 —— 量化到 6 位是 0.000000
    result = cost(0, 4, 0, table=split())

    assert result.gap is CostGap.TOO_SMALL
    assert result.total is None


def test_zero_usage_is_a_real_zero():
    """三档都报过、值就是零 -> 金额是一个**说得出口的 0** (不是算不出来)."""
    result = cost(0, 0, 0)

    assert result.known
    assert result.total == Decimal("0")


# ---------------------------------------------------------------------------
# 缺东西的两种情形
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["cache_miss_tokens", "cache_hit_tokens", "output_tokens"]
)
def test_one_missing_component_blocks_the_whole_amount(field: str):
    """三档里缺一个就算不出金额, 并**点名**缺的是哪个.

    这里刻意**不给输入总量**: 缓存那两档在输入总量在的时候是能推出来的, 所以只有
    连总量都缺时它们才真算不出来.
    """
    values: dict[str, int | None] = {
        "input_tokens": None,
        "cache_miss_tokens": 100,
        "cache_hit_tokens": 100,
        "output_tokens": 100,
    }
    values[field] = None

    result = cost_of(
        model="m",
        table=flat(),
        moment=bj("2026-09-21 10:00"),
        **values,
    )

    assert not result.known
    assert result.gap is CostGap.NO_TOKENS
    assert result.missing == (field,)
    assert result.total is None


def test_every_missing_component_is_named():
    """缺好几个时一次说全 (省得改一个再跑一次才发现还缺另一个)."""
    result = cost(None, 100, None)

    assert result.gap is CostGap.NO_TOKENS
    assert result.missing == ("cache_miss_tokens", "output_tokens")


def test_a_missing_cache_tier_is_derived_from_the_input_total():
    """上游只报「命中」不报「未命中」时 (OpenAI 系的形状), 未命中 = 输入 - 命中.

    为什么这不是「猜」: 两个减数都是**上游报的**, 而上游自己的数字就满足这条恒等式
    (实测五组样本: 128+248=376 · 0+400=400 · 256+208=464 · 0+18=18 · 256+241=497).
    若不补这一档, 换一家只给 `prompt_tokens_details.cached_tokens` 的上游, 金额就
    **永远**报「算不出来」.
    """
    derived = cost(None, 300, 59, input_tokens=400)
    explicit = cost(100, 300, 59)

    assert derived.known and explicit.known
    assert derived.line_of["cache_miss"].tokens == 100
    assert derived.total == explicit.total
    assert derived.derived == ("cache_miss_tokens",), "要标明这一档是减出来的"


def test_the_derivation_works_from_either_side():
    """反过来 (报了未命中、没报命中) 同一条规则补."""
    derived = cost(100, None, 59, input_tokens=400)

    assert derived.line_of["cache_hit"].tokens == 300
    assert derived.derived == ("cache_hit_tokens",)


def test_the_derivation_only_fires_when_the_tier_is_actually_missing():
    """三个数**都报了**时原样用报的值, 不拿输入总量去改写 (报的优先)."""
    result = cost(100, 300, 59, input_tokens=99999)

    assert result.line_of["cache_hit"].tokens == 300
    assert result.line_of["cache_miss"].tokens == 100
    assert result.derived == ()


def test_a_negative_derivation_counts_as_missing():
    """减出来是负数 (报了命中多于输入总量 —— 上游数据不对) 按缺处理.

    给个负 token 数会让金额变成负数, 而负的金额看起来也像个答案.
    """
    result = cost(None, 500, 0, input_tokens=100)

    assert result.gap is CostGap.NO_TOKENS
    assert result.missing == ("cache_miss_tokens",)


def test_the_derivation_needs_the_input_total():
    """输入总量也没报 -> 补不出来, 照旧报缺哪一档."""
    result = cost(None, 300, 0)

    assert result.gap is CostGap.NO_TOKENS
    assert result.missing == ("cache_miss_tokens",)


def test_the_function_has_no_slot_for_reasoning_tokens():
    """`reasoning_tokens` **连参数位都没有**: 重复计价没有入口 (传进来 TypeError).

    光靠「输出价里已经含了推理」这句话挡不住后来的人顺手加一个参数, 而加完金额
    偏高且不报错 —— 这一条把那个入口焊死.
    """
    with pytest.raises(TypeError):
        cost_of(
            model="m",
            table=flat(),
            moment=bj("2026-09-21 10:00"),
            input_tokens=None,
            cache_miss_tokens=1,
            cache_hit_tokens=1,
            output_tokens=1,
            reasoning_tokens=5,
        )


# ---------------------------------------------------------------------------
# 峰谷: 看运行开始那一刻 + 中国日历说了算
# ---------------------------------------------------------------------------


def test_the_two_price_sets_give_different_money_for_the_same_usage():
    """同一批 token, 峰时段与谷时段是两笔钱 (档位也记下来, 免得事后说不清)."""
    usage = {"cache_miss": 1_000_000, "cache_hit": 0, "output": 0}
    peak = cost(**usage, moment=bj("2026-09-21 10:00"), table=split())
    valley = cost(**usage, moment=bj("2026-09-21 13:00"), table=split())

    assert peak.tier == "peak" and valley.tier == "valley"
    assert peak.total == Decimal("2"), "100 万未命中 x 2/M (峰)"
    assert valley.total == Decimal("1"), "同上 x 1/M (谷)"


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("08:59", "valley"),  # 差一分钟
        ("09:00", "peak"),  # 起点含
        ("11:59", "peak"),
        ("12:00", "valley"),  # 终点不含
        ("13:59", "valley"),
        ("14:00", "peak"),  # 第二个峰窗
        ("17:59", "peak"),
        ("18:00", "valley"),  # 终点不含
        ("23:30", "valley"),
        ("00:30", "valley"),
    ],
)
def test_peak_window_boundaries(clock: str, expected: str):
    """峰窗是**半开区间** `[起, 止)`: 9:00 整算峰、12:00 整算谷.

    边界写死在用例里, 是因为「9 点到 12 点」这句话在实现上有四种读法 —— 挑一种
    并钉住, 比事后争论便宜.
    """
    result = cost(1_000_000, 0, 0, moment=bj(f"2026-09-21 {clock}"), table=split())

    assert result.tier == expected


def test_a_rest_day_is_valley_all_day():
    """**2026-09-25 是周五, 但日历说它是休息日** —— 整天按谷价.

    这一条是「为什么必须用日历库」最直白的证据: 拿「周一到周五 = 工作日」硬判,
    这一天会被算成峰价, 而账单不是那么开的.
    """
    result = cost(1_000_000, 0, 0, moment=bj("2026-09-25 10:00"), table=split())

    assert result.tier == "valley"
    assert result.total == Decimal("1")


def test_an_adjusted_workday_on_a_weekend_is_peak():
    """**2026-09-20 是周日, 但调休上班** —— 峰窗内按峰价."""
    result = cost(1_000_000, 0, 0, moment=bj("2026-09-20 10:00"), table=split())

    assert result.tier == "peak"


def test_a_national_holiday_is_valley():
    """国庆当天 (2026-10-01, 周四) 是休息日 -> 谷价."""
    result = cost(1_000_000, 0, 0, moment=bj("2026-10-01 10:00"), table=split())

    assert result.tier == "valley"


def test_the_tier_comes_from_the_start_moment_in_the_configured_timezone():
    """判定用的是**运行开始那一刻** (UTC 存的), 先转成配置时区再判.

    09:00 UTC 是北京时间 17:00 (峰), 而同一刻在 UTC 那边看是 9 点 —— 不转换的
    实现会给出另一个答案.
    """
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)  # = 北京时间 17:00
    result = cost(1_000_000, 0, 0, moment=moment, table=split())

    assert moment.astimezone(SHANGHAI).hour == 17
    assert result.tier == "peak"


def test_a_flat_model_ignores_the_rule():
    """同一份配置里可以既有峰谷模型又有全天一价的模型 (后者不看时段)."""
    both = PriceTable.from_json(
        json.dumps(
            {
                "timezone": "Asia/Shanghai",
                "peak_windows": [["09:00", "12:00"]],
                "models": {
                    "split-model": {
                        "peak": {"cache_miss": 2, "cache_hit": 0.04, "output": 8},
                        "valley": {"cache_miss": 1, "cache_hit": 0.02, "output": 4},
                    },
                    "flat-model": {"cache_miss": 3, "cache_hit": 0.3, "output": 9},
                },
            }
        )
    )

    flat = cost(1_000_000, 0, 0, table=both, model="flat-model")
    split_model = cost(1_000_000, 0, 0, table=both, model="split-model")

    assert flat.tier is None and flat.total == Decimal("3")
    assert split_model.tier == "peak"


# ---------------------------------------------------------------------------
# 日历判不了 / 没单价 / 没开始时刻
# ---------------------------------------------------------------------------


def test_out_of_range_year_cannot_be_priced():
    """日历里没有 2027 年 (数据只到 2026) -> 算不出来, 并带上「哪一天」.

    不按「周末」硬猜: 猜出来的峰谷在长假期间会错, 而错的金额看起来和真的一样.
    """
    result = cost(1_000_000, 0, 0, moment=bj("2027-01-05 10:00"), table=split())

    assert result.gap is CostGap.NO_CALENDAR_DATA
    assert result.date == "2027-01-05"
    assert result.total is None


def test_missing_calendar_library_cannot_be_priced(monkeypatch):
    """配了峰谷价但没装日历依赖 -> 算不出来, 原因是「库没装」(不是「年份没有」)."""
    monkeypatch.setitem(sys.modules, "chinese_calendar", None)

    result = cost(1_000_000, 0, 0, moment=bj("2026-09-21 10:00"), table=split())

    assert result.gap is CostGap.NO_CALENDAR_LIB
    assert result.total is None


def test_a_flat_table_does_not_need_the_calendar(monkeypatch):
    """全天一价的部署用不着日历 —— 库没装也照样算得出金额."""
    monkeypatch.setitem(sys.modules, "chinese_calendar", None)

    result = cost(1_000_000, 0, 0, table=flat())

    assert result.known and result.total == Decimal("2")


def test_without_a_start_moment_the_tier_cannot_be_judged():
    """不知道开始时刻 -> 判不了峰谷 (不拿「现在」顶替)."""
    result = cost(1_000_000, 0, 0, moment=None, table=split())

    assert result.gap is CostGap.NO_MOMENT


def test_unknown_model_reports_no_price():
    """价目表里没有这个模型 -> 报「没配单价」, 不是 0."""
    result = cost(1_000_000, 0, 0, model="没配过的模型")

    assert result.gap is CostGap.NO_PRICE
    assert result.model == "没配过的模型"


def test_empty_price_table_reports_no_price():
    """价目表整个是空的 (环境变量没配) 时同样报「没配单价」."""
    result = cost(1_000_000, 0, 0, table=PriceTable())

    assert result.gap is CostGap.NO_PRICE


def test_run_without_a_model_name_reports_no_price():
    """运行行里没记模型名 -> 也是「没配单价」, 展示层据此说「这次没记模型名」."""
    result = cost(1_000_000, 0, 0, model=None)

    assert result.gap is CostGap.NO_PRICE
    assert result.model is None


def test_a_broken_table_is_reported_as_such():
    """价目表压根没读成 (调用方降级成空表 + 带着原因) -> 原因要一路带到明细里.

    与「这个模型没配」分开: 一个要去改配置里的语法, 一个要去补一行价.
    """
    broken = PriceTable(error="价目表里 'm' 少了这些档: output")

    result = cost(1_000_000, 0, 0, table=broken)

    assert result.gap is CostGap.BAD_CONFIG
    assert "output" in (result.error or "")


# ---------------------------------------------------------------------------
# 落库的明细: 写进去与读回来是一对
# ---------------------------------------------------------------------------


def test_detail_round_trips_a_computed_cost():
    """算出来的那份明细写完再读回来, 逐项一致 (trace 就是靠这条读库的)."""
    original = cost(423, 3_072, 182, table=split(), moment=bj("2026-09-21 10:00"))

    back = RunCost.from_detail(original.to_detail())

    assert original.known and back.known
    assert back.tier == original.tier
    assert back.total == original.total
    assert [line.tier for line in back.lines] == ["cache_miss", "cache_hit", "output"]
    assert back.line_of["output"].tokens == 182
    assert back.line_of["output"].price == original.line_of["output"].price


def test_detail_round_trips_a_gap():
    """算不出来那一份也要能读回来 (原因与上下文都在)."""
    original = cost(1_000_000, 0, 0, moment=bj("2027-01-05 10:00"), table=split())

    back = RunCost.from_detail(original.to_detail())

    assert back.gap is CostGap.NO_CALENDAR_DATA
    assert back.date == "2027-01-05"


def test_detail_stores_money_as_strings():
    """金额与单价在库里是**字符串** —— JSON 的数字是浮点, 而钱不能过浮点."""
    detail = cost(423, 0, 0, table=split()).to_detail()

    assert isinstance(detail["total"], str)
    assert isinstance(detail["lines"][0]["price"], str)
    assert isinstance(detail["lines"][0]["amount"], str)


@pytest.mark.parametrize(
    "detail",
    [
        {},
        {"kind": "什么鬼"},
        {"kind": "cost"},
        {"kind": "gap"},
        42,
        {"kind": "cost", "total": "不是数"},
    ],
)
def test_an_unreadable_detail_reports_itself_instead_of_crashing(detail):
    """库里那份明细读不懂 -> 给一个「读不懂」的结果, 不炸 (老格式 / 坏数据).

    只读入口对着的可能是很久以前写的行, 一条读不出来的明细不该让整段打不出来.
    """
    result = RunCost.from_detail(detail)

    assert not result.known
    assert result.gap is CostGap.UNKNOWN_DETAIL


def test_no_detail_at_all_is_not_the_same_as_an_unreadable_one():
    """明细压根没有 (`None`) -> 报「没有账」, **不是**「坏数据」.

    这一栏为空的正常运行是有的: 那一趟还没收尾 (进程被硬杀 / 正在跑), 或者它是
    改口径之前写的. 把它说成坏数据会让人去查一个根本没坏的地方.
    """
    result = RunCost.from_detail(None)

    assert result.gap is CostGap.NO_DETAIL


# ---------------------------------------------------------------------------
# 价目表解析
# ---------------------------------------------------------------------------


def test_price_table_is_empty_without_the_variable():
    """没配环境变量 -> 空表 (于是每个模型都报「没配单价」, 而不是报 0)."""
    assert PriceTable.from_env({}).models == {}
    assert PriceTable.from_env({ENV_MODEL_PRICES: ""}).models == {}


def test_price_table_reads_both_shapes():
    """六档与三档可以混着放 (按模型), 读出来各自成形状."""
    table = PriceTable.from_json(SPLIT_JSON)

    assert table.tier_needed("m") is True
    assert table.price_for("m", "peak").output == Decimal("8")
    assert table.price_for("m", "valley").output == Decimal("4")
    assert table.rule is not None
    assert table.rule.timezone == "Asia/Shanghai"


def test_a_flat_only_table_has_no_rule():
    """只配全天一价 -> 不建规则 (也就不需要日历)."""
    table = flat()

    assert table.rule is None
    assert table.tier_needed("m") is False
    assert table.price_for("m", None).output == Decimal("8")


def test_a_flat_model_is_found_even_when_the_rule_exists():
    """混着放时, 全天一价那个模型不看时段也能查到 (查法只有一处)."""
    table = PriceTable.from_json(
        json.dumps(
            {
                "timezone": "Asia/Shanghai",
                "peak_windows": [["09:00", "12:00"]],
                "models": {
                    "a": {
                        "peak": {"cache_miss": 2, "cache_hit": 0.04, "output": 8},
                        "valley": {"cache_miss": 1, "cache_hit": 0.02, "output": 4},
                    },
                    "b": {"cache_miss": 3, "cache_hit": 0.3, "output": 9},
                },
            }
        )
    )

    assert table.price_for("b", "peak") == table.price_for("b", "valley")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("不是 JSON", "JSON"),
        ("[1, 2]", "对象"),
        ('{"单价": {}}', "不认识的键"),
        ('{"models": []}', "对象"),
        ('{"timezone": "Asia/Shanghai"}', "models"),
        ('{"models": {"m": true}}', "对象"),
        (
            '{"models": {"m": {"cache_miss": 1, "cache_hit": 0.5}}}',
            "output",
        ),
        (
            '{"models": {"m": {"cache_miss": 1, "cache_hit": 0.5,'
            ' "output": 8, "in": 2}}}',
            "in",
        ),
        (
            '{"models": {"m": {"cache_miss": -1, "cache_hit": 0.5, "output": 8}}}',
            "负",
        ),
        (
            '{"models": {"m": {"cache_miss": "便宜", "cache_hit": 0.5, "output": 8}}}',
            "数字",
        ),
        (
            '{"models": {"m": {"cache_miss": true, "cache_hit": 0.5, "output": 8}}}',
            "数字",
        ),
        # 峰谷那一族: 缺一套 / 混层 / 缺规则 / 时区名 / 峰窗形状
        (
            '{"timezone": "Asia/Shanghai", "peak_windows": [["09:00", "12:00"]],'
            ' "models": {"m": {"peak": {"cache_miss": 2, "cache_hit": 0.04,'
            ' "output": 8}}}}',
            "valley",
        ),
        (
            '{"timezone": "Asia/Shanghai", "peak_windows": [["09:00", "12:00"]],'
            ' "models": {"m": {"peak": {"cache_miss": 2, "cache_hit": 0.04,'
            ' "output": 8}, "cache_miss": 1}}}',
            "混",
        ),
        (
            '{"models": {"m": {"peak": {"cache_miss": 2, "cache_hit": 0.04,'
            ' "output": 8}, "valley": {"cache_miss": 1, "cache_hit": 0.02,'
            ' "output": 4}}}}',
            "peak_windows",
        ),
        (
            '{"timezone": "火星/上海", "peak_windows": [["09:00", "12:00"]],'
            ' "models": {"m": {"peak": {"cache_miss": 2, "cache_hit": 0.04,'
            ' "output": 8}, "valley": {"cache_miss": 1, "cache_hit": 0.02,'
            ' "output": 4}}}}',
            "时区名",
        ),
        (
            '{"timezone": "Asia/Shanghai", "peak_windows": [],'
            ' "models": {"m": {"peak": {"cache_miss": 2, "cache_hit": 0.04,'
            ' "output": 8}, "valley": {"cache_miss": 1, "cache_hit": 0.02,'
            ' "output": 4}}}}',
            "非空",
        ),
        (
            '{"timezone": "Asia/Shanghai", "peak_windows": [["9点", "12:00"]],'
            ' "models": {"m": {"peak": {"cache_miss": 2, "cache_hit": 0.04,'
            ' "output": 8}, "valley": {"cache_miss": 1, "cache_hit": 0.02,'
            ' "output": 4}}}}',
            "HH:MM",
        ),
        (
            '{"timezone": "Asia/Shanghai", "peak_windows": [["12:00", "09:00"]],'
            ' "models": {"m": {"peak": {"cache_miss": 2, "cache_hit": 0.04,'
            ' "output": 8}, "valley": {"cache_miss": 1, "cache_hit": 0.02,'
            ' "output": 4}}}}',
            "起止",
        ),
    ],
)
def test_malformed_price_table_is_rejected_at_load_time(raw: str, reason: str):
    """写错的价目表**当场**报错, 并说清错在哪一条.

    为什么不容忍 (跳过这一条 / 按 0 算): 静默丢掉一个模型, 表现是「这个运行报
    『没配单价』」—— 使用者会去翻配置, 而配置明明写着. 缺一档按 0 算则更糟:
    金额偏低且看起来完全正常. 这两种都要在**读配置的那一刻**拦住.
    """
    with pytest.raises(DataConfigError) as info:
        PriceTable.from_json(raw)

    assert reason in str(info.value)


def test_peak_windows_are_half_open_ranges():
    """峰窗读出来就是半开区间 (起点含、终点不含) —— 判定照它比."""
    windows = split().rule.windows

    assert [(start.hour, start.minute) for start, _ in windows] == [(9, 0), (14, 0)]
    assert [(end.hour, end.minute) for _, end in windows] == [(12, 0), (18, 0)]


def test_peak_rule_can_be_built_by_hand():
    """规则对象本身可以手搓 (用例与将来的运维脚本都需要)."""
    from datetime import time

    rule = PeakRule(timezone="Asia/Shanghai", windows=((time(9, 0), time(12, 0)),))

    assert rule.tier_at(bj("2026-09-21 10:00")).tier == "peak"
    assert rule.tier_at(bj("2026-09-21 15:00")).tier == "valley"


def test_tier_at_returns_a_verdict_without_raising():
    """判不了时**不抛异常**, 而是回一个带原因的裁决 (调用方自己决定拦还是降级)."""
    verdict = split().rule.tier_at(bj("2027-01-05 10:00"))

    assert verdict.tier is None
    assert verdict.gap is CostGap.NO_CALENDAR_DATA


# ---------------------------------------------------------------------------
# 启动自检
# ---------------------------------------------------------------------------


def test_ready_check_passes_for_an_empty_table():
    """没配价目表 -> 通过 (不配价不是错误, 只是没有金额)."""
    ensure_pricing_ready(PriceTable())


def test_ready_check_passes_for_flat_only_tables():
    """只配全天一价 -> 通过 (用不着日历, 也就不用查)."""
    ensure_pricing_ready(flat())


def test_ready_check_passes_when_the_calendar_covers_the_moment():
    """日历覆盖那一刻 -> 通过."""
    ensure_pricing_ready(split(), moment=bj("2026-09-21 10:00"))


def test_ready_check_fails_on_a_broken_table():
    """价目表没读成 -> 当场拦住 (由调用方拒绝启动)."""
    with pytest.raises(PricingNotReadyError) as info:
        ensure_pricing_ready(PriceTable(error="少了 output 档"))

    assert "output" in str(info.value)


def test_ready_check_fails_when_the_calendar_is_too_old():
    """日历里没有这一刻所在的那一年 -> 拦住, 并给一句能照着做的修法.

    为什么要在启动时拦 (而不是等收尾发现): 那一趟的钱会空着, 而空着的钱要人工补
    —— 与其如此, 不如在进程还没接活的时候就报出来.
    """
    with pytest.raises(PricingNotReadyError) as info:
        ensure_pricing_ready(split(), moment=bj("2027-01-05 10:00"))

    assert "2027-01-05" in str(info.value)
    assert "chinesecalendar" in str(info.value)


def test_ready_check_fails_when_the_library_is_missing(monkeypatch):
    """配了峰谷价但依赖没装 -> 拦住, 报错里给出安装命令."""
    monkeypatch.setitem(sys.modules, "chinese_calendar", None)

    with pytest.raises(PricingNotReadyError) as info:
        ensure_pricing_ready(split(), moment=bj("2026-09-21 10:00"))

    # 报的是**装的那个名字** (`chinesecalendar`, 无下划线), 不是 import 的那个
    assert "chinesecalendar" in str(info.value)
    assert "pricing" in str(info.value), "可选组也要说清楚"


def test_ready_check_message_names_the_install_extra():
    """报错要能直接抄 (本仓那条「报错信息含足够上下文」的规矩)."""
    with pytest.raises(PricingNotReadyError) as info:
        ensure_pricing_ready(split(), moment=bj("2027-06-01 10:00"))

    assert "pip install -U chinesecalendar" in str(info.value)


def test_ready_check_uses_now_by_default():
    """不给时刻就用当下 —— 今天 (2026) 在数据范围内, 所以通过."""
    ensure_pricing_ready(split())


def test_precise_boundary_uses_utc_offsets():
    """再钉一条时区换算: 北京时间 09:00 那一刻, UTC 是 01:00 (同一天的不同写法)."""
    moment = bj("2026-09-21 09:00")

    assert moment.astimezone(UTC) == datetime(2026, 9, 21, 1, 0, tzinfo=UTC)
    assert cost(1_000_000, 0, 0, moment=moment, table=split()).tier == "peak"


def test_a_moment_just_before_midnight_belongs_to_its_own_day():
    """跨天判定: 北京时间 23:59 仍算那一天 (谷), 不会滑到第二天去."""
    result = cost(1_000_000, 0, 0, moment=bj("2026-09-21 23:59"), table=split())

    assert result.tier == "valley"


def test_long_runs_are_priced_by_their_start():
    """跑多久不影响档位: 开始时刻落在峰窗内, 整趟就是峰价 (结束时刻不参与).

    11:59 开始、跑了三个小时 (结束在谷时段) —— 但只要判据是**开始时刻**, 就是峰价.
    这也是「按开始时刻算」这条口径唯一能被看见的地方.
    """
    start = bj("2026-09-21 11:59")
    ended = start + timedelta(hours=3)

    result = cost(1_000_000, 0, 0, moment=start, table=split())

    assert ended.astimezone(SHANGHAI).hour == 14, "结束时刻确实已经出峰窗了"
    assert result.tier == "peak"


def test_the_verdict_date_is_the_local_one():
    """裁决里带的那一天是**配置时区**下的日期 (报「哪一年没数据」要用它)."""
    verdict = split().rule.tier_at(datetime(2026, 9, 21, 16, 30, tzinfo=UTC))

    assert verdict.date == "2026-09-22", "UTC 16:30 = 北京时间第二天 00:30"
    assert verdict.tier == "valley"


def test_timezone_other_than_shanghai():
    """换一个时区也能判 (规则里那个时区是被真的用上的, 不是写死的)."""
    table = PriceTable.from_json(
        json.dumps(
            {
                "timezone": "UTC",
                "peak_windows": [["09:00", "12:00"]],
                "models": {
                    "m": {
                        "peak": {"cache_miss": 2, "cache_hit": 0.04, "output": 8},
                        "valley": {"cache_miss": 1, "cache_hit": 0.02, "output": 4},
                    }
                },
            }
        )
    )

    # UTC 09:30 = 北京时间 17:30 (峰), 但在 UTC 规则下是峰窗内 -> 峰
    assert (
        cost(1, 0, 0, moment=datetime(2026, 9, 21, 9, 30, tzinfo=UTC), table=table).tier
        == "peak"
    )
    # UTC 13:00 = 北京时间 21:00 -> 在 UTC 规则下已出峰窗 -> 谷
    assert (
        cost(1, 0, 0, moment=datetime(2026, 9, 21, 13, 0, tzinfo=UTC), table=table).tier
        == "valley"
    )


def test_naive_moment_is_treated_as_local_to_the_rule_timezone():
    """不带时区的时刻按**规则那个时区**理解 (用例与手工造数最常见的形状).

    为什么不是报错: 手搓一个 `datetime(2026, 9, 21, 10, 0)` 意思明显就是「那天的
    10 点」, 而把它当成 UTC 会悄悄差 8 小时 —— 拿不准就按配置的时区读, 至少与
    规则本身自洽.
    """
    rule = split().rule

    assert rule.tier_at(datetime(2026, 9, 21, 10, 0)).tier == "peak"
    assert rule.tier_at(datetime(2026, 9, 21, 20, 0)).tier == "valley"


def test_timezone_aware_naive_comparison_does_not_explode():
    """时刻带 UTC 偏移与不带混着来都不炸 (实现里把 tzinfo 抹掉再比)."""
    aware = datetime(2026, 9, 21, 10, 0, tzinfo=timezone(timedelta(hours=8)))

    assert split().rule.tier_at(aware).tier == "peak"
