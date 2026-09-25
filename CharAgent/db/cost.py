"""成本口径: 三档单价 + 峰谷两套价, 在**运行收尾那一刻**算好写进运行行 (ticket 28).

一句话理解: 这个文件回答「这一次运行花了多少钱」, 而答案在运行结束的那一瞬间就
**写死在库里** —— 之后谁来看、隔多久来看, 都是同一个数.

**为什么是收尾算好写死, 而不是查询时现算** (2026-09-25 改判, 推翻本片原先那条):

- 账单是按**当时那一版价目表**开的. 查询侧现算的代价是「换个价目表, 历史运行的金
  额跟着变」—— 那与账单永远对不上, 而「这笔钱与账单对得上吗」正是这个功能存在的
  理由 (旧的 ADR-0005 说「成本只加归因, 不改账」, 那时担心的「写死了就只剩一种
  答案」在**对账**这个用途下恰恰是优点: 只要那一种就是账单那一种).
- 峰谷价更逼着我们把「算的那一刻」固定下来: 同一批 token, 工作日 10 点跑与凌晨跑
  是两个价 —— 那是**一次运行的属性**, 不写下来的话, 事后连「这笔该按峰还是谷算」
  都无从复原 (判据是运行的开始时刻, 而那一刻只有 run 行知道).

于是本模块有两个调用方, 各自的位置都很明确:

| 谁 | 什么时候 | 做什么 |
|---|---|---|
| 记录员 (`db/recorder.py`) | 运行**收尾那一拍** | 算金额 + 算式, 一起写进 runs 的两列 |
| 业务侧进程启动 | 进循环**之前** | `ensure_pricing_ready`: 日历过期 / 写错就拦住 |

`client/trace.py` **不算钱**: 它只读库里那两列, 连价目表与日历都不碰 (当初「现算」
那条路已经拆掉). 它借用本模块的两个类型 (`RunCost` / `CostGap`) 把那份明细读成
结构化对象 —— 那是读库, 不是算钱.

## 价目表

从环境变量 `CHARAGENT_MODEL_PRICES` 读, 默认空 (空 = 一个模型都没配价, 运行照跑、
金额空着并写明原因):

```json
{"timezone": "Asia/Shanghai",
 "peak_windows": [["09:00", "12:00"], ["14:00", "18:00"]],
 "models": {
   "deepseek-flash": {"peak":   {"cache_miss": 2, "cache_hit": 0.04, "output": 8},
                      "valley": {"cache_miss": 1, "cache_hit": 0.02, "output": 4}},
   "不分峰谷的模型":  {"cache_miss": 1, "cache_hit": 0.1, "output": 2}}}
```

- **三档** = 未命中缓存的输入 / 命中缓存的输入 / 输出, 单位是**每百万 token** 的
  金额 (与各家价目表同形). 金额量化到 6 位小数, 与 `total_cost` 的 `Numeric(14, 6)`
  同标度.
- **两套价 (peak / valley)**: 一个模型要么写三档 (全天一个价), 要么写 peak +
  valley 两套. **用了两套就必须给 `timezone` 与 `peak_windows`** —— 否则会拿一套
  规则静默算错钱, 而那正是本文开头要防的.
- **判峰谷看运行开始那一刻** (`runs.created_at`), 与该次运行跑多久无关 (跨过 12 点
  的运行整趟按开始时刻算). 判据 = 「那一刻的日期是不是中国的工作日」(法定节假日
  不算、调休上班的周末算) 且「时刻落在峰窗内」.
- **日历数据只到 2026** (`chinesecalendar` 每年随国务院的安排发一版, 国内 ~11 月):
  超出范围判不了 → 金额空着并写明「升级依赖」, 而不是按周末硬猜.
- 币种不换算: 屏幕上的 `¥` 是写死的, 所以表里的数字必须是人民币 (一处符号换一个
  部署侧配置项不值当, 但这是个**已知的耦合**, 记在 ticket 28 与 ADR-0018 里).

**`reasoning_tokens` 不参与乘法** (2026-09-24 核实): 它是 `output_tokens` 的**明细**
—— 上游放在 `usage.completion_tokens_details` 下, 而这个层级在 schema 上就是
`completion_tokens` 的分解; 实测样本 `completion_tokens = 59` 而其中
`reasoning_tokens = 16`, 且上游自己给的 `total_tokens` 恰好等于
`prompt_tokens + completion_tokens` (459 = 400 + 59). 再单乘一遍就会**系统性偏高**,
而偏高与偏低都不会报错. 本模块因此连这个参数都不收: 传进来会直接 TypeError.

**算不出来就说算不出来**: 缺单价 / 缺分量 / 日历判不了 …一律给 `CostGap` 原因,
绝不给 0 ——「报 0 比报不出来更糟, 因为 0 看起来像个答案」(ADR-0005 那条纪律, 换个
口径之后仍然成立).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from CharAgent.db.errors import DataConfigError, PricingNotReadyError

# 读价目表的环境变量 (与 db/config.py 的 ENV_* 同一套命名法: 名字带 CHARAGENT_ 前缀)
ENV_MODEL_PRICES = "CHARAGENT_MODEL_PRICES"

# 单价的计价单位: 每 **百万** token. 与各家的价目表同形 —— 部署侧照抄价目表上的
# 数字即可, 不必自己先换算成「每 token」(那是 1e-6 量级的小数, 手抄必错).
TOKENS_PER_PRICE_UNIT = 1_000_000

# 金额的小数位: 与 charagent_runs.total_cost 的 Numeric(14, 6) 同标度 ——
# 写进那一列的数与这里算出来的数完全一致
_AMOUNT_SCALE = Decimal("0.000001")

# 三档的名字 (**唯一一处定义**): 配置的键、报「缺哪一档」用的列名都从这里取
_TIERS = ("cache_miss", "cache_hit", "output")

# 三档各自的计价分量是哪一列 (报缺时的名字, 与 runs 的列同名)
_COMPONENT_OF_TIER = {
    "cache_miss": "cache_miss_tokens",
    "cache_hit": "cache_hit_tokens",
    "output": "output_tokens",
}

# 时段的两种名字 + 一个内部记号: FLAT = 「这一个模型全天一个价」(配置里写三档).
# 它不是供应商那边的概念, 只在价目表内部当键用, 不会出现在屏幕上.
PEAK = "peak"
VALLEY = "valley"
FLAT = "flat"

# 峰谷规则与价的三个配置键 (顶层)
_KEY_TIMEZONE = "timezone"
_KEY_PEAK_WINDOWS = "peak_windows"
_KEY_MODELS = "models"


class CostGap(StrEnum):
    """算不出金额的原因 (措辞在展示层, 这里只说是什么).

    分成这么细是因为**修法各不相同**: 没配价要去填配置, 缺分量要去查上游为什么
    没上报, 日历过期要去升级依赖 —— 合成一句「算不出来」等于把排查方向也吞了.
    """

    NO_PRICE = "no_price"  # 这个模型不在价目表里 (或价目表压根是空的)
    NO_TOKENS = "no_tokens"  # 三档里至少有一档上游没上报, 拆不开就算不出来
    NO_CALENDAR_DATA = "no_calendar_data"  # 日历里没有这一天所在的年份 (该升级依赖了)
    NO_CALENDAR_LIB = "no_calendar_lib"  # 配了峰谷价, 但没装日历依赖
    NO_MOMENT = "no_moment"  # 不知道这次运行从哪一刻开始 (判不了峰谷)
    UNFINISHED = "unfinished"  # 没跑完的运行: 没有账目, 也就没有金额
    BAD_CONFIG = "bad_config"  # 价目表压根没读成 (写错了)
    NO_DETAIL = "no_detail"  # 那一行没有金额也没有明细 (没收尾 / 改口径之前写的)
    UNKNOWN_DETAIL = "unknown_detail"  # 库里那份明细读不懂 (老格式 / 坏数据)
    TOO_SMALL = "too_small"  # 算出来的钱小于这一列的最小刻度 (1e-6), 存不下


@dataclass(frozen=True, slots=True)
class TierPrices:
    """一个模型的**三档单价**, 单位都是「每百万 token 的金额」.

    三档分别是: 未命中缓存的输入 / 命中缓存的输入 / 输出. 三者没有默认值 ——
    少一档就定不了价, 缺的那档按 0 算会让金额偏低且看起来完全正常, 所以在
    `PriceTable.from_env` 那一刻就拦住 (单价不能是负的, 同一条规矩).

    attributes:
        cache_miss: 未命中缓存的输入单价 (通常是最贵的那档).
        cache_hit: 命中缓存的输入单价 (命中复用前缀, 各家都远低于未命中).
        output: 输出单价 (含思维链 —— 见模块 docstring 那条核实).
    """

    cache_miss: Decimal
    cache_hit: Decimal
    output: Decimal


@dataclass(frozen=True, slots=True)
class TierVerdict:
    """「运行开始那一刻算峰还是谷」的裁决 (判不了时带原因).

    attributes:
        tier: `peak` / `valley`; None = 判不了 (原因见 `gap`).
        gap: 判不了的原因; 判出来了就是 None.
        date: 判的是哪一天 (`YYYY-MM-DD`, 已转成配置时区); 判不了时也带上 ——
            「哪一年没数据」是那句报错里最有用的信息.
    """

    tier: str | None
    gap: CostGap | None = None
    date: str | None = None


@dataclass(frozen=True, slots=True)
class PeakRule:
    """峰谷规则: 哪个时区的哪些时段算峰 (工作日的判定交给中国节假日日历).

    为什么规则由**部署侧**给而不是框架内置: 「哪个时段算贵」是供应商的作息, 不是
    框架的常识 —— 内置一套默认值, 换家供应商就会静默算错钱.

    attributes:
        timezone: IANA 时区名 (`Asia/Shanghai`); 供应商多半按它自己所在地的作息
            定价, 而库里存的是 UTC, 判定前必须转过去.
        windows: 峰时段, 半开区间 `[start, end)` (9:00 算峰、12:00 整算谷).
    """

    timezone: str
    windows: tuple[tuple[time, time], ...]

    def __post_init__(self) -> None:
        """时区名在这儿验掉 (手搓的规则也一样, 不必等到判的时候才炸).

        Raises:
            DataConfigError: 时区名不认识 (用 IANA 名, 如 `Asia/Shanghai`).
        """
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise DataConfigError(
                f'不认识这个时区名: {self.timezone!r} (用 IANA 名, 如 "Asia/Shanghai")'
            ) from exc

    def tier_at(self, moment: datetime | None) -> TierVerdict:
        """某一刻算峰还是谷 (工作日 + 落在峰窗内 = 峰, 其余是谷).

        Args:
            moment: 要判的时刻 (本次运行的**开始时刻**, 带时区); None = 不知道
                (老数据 / 调用方没给) —— 判不了, 不是「算谷」.

        Returns:
            TierVerdict: 裁决; 判不了时 `tier` 是 None 而 `gap` 说明原因.
        """
        if moment is None:
            return TierVerdict(tier=None, gap=CostGap.NO_MOMENT)
        zone = ZoneInfo(self.timezone)
        # 不带时区的时刻按**规则这个时区**理解: 手搓一个 `datetime(2026, 9, 21, 10)`
        # 意思明显是「那天的 10 点」, 而 Python 默认会当成本机时区 —— 那会悄悄差
        # 好几个小时, 而差出来的档位看起来很正常. 库里存的一律是带时区的, 走到这个
        # 分支的只有测试与手工造的数
        local = (
            moment.replace(tzinfo=zone)
            if moment.tzinfo is None
            else moment.astimezone(zone)
        )
        day = local.date()
        stamp = day.isoformat()
        workday = _is_workday(day)
        if isinstance(workday, CostGap):
            # 判不了: 日历里没有这一年 (数据只到 2004-2026, 每年要升级), 或依赖没装
            return TierVerdict(tier=None, gap=workday, date=stamp)
        if not workday:
            # 周末与法定节假日整天空闲 —— 谷价 (调休上班的周末在上面那一步已经是
            # True 了, 所以这里不用再判一次)
            return TierVerdict(tier=VALLEY, date=stamp)
        # `datetime.time()` 给的是一个**不带时区**的时刻 (即使上面那个带), 于是
        # 下面这一比是纯「墙上时间」比窗口 —— 时区换算已经在 astimezone 那一步做完
        clock = local.time()
        for start, end in self.windows:
            if start <= clock < end:
                return TierVerdict(tier=PEAK, date=stamp)
        return TierVerdict(tier=VALLEY, date=stamp)


def _is_workday(day: Any) -> bool | CostGap:
    """这一天是不是中国的工作日; 判不了时回一个 `CostGap` 说明为什么.

    返回值而不是抛异常: 「判不了」在本模块是**正常结局之一** (金额空着并写明原因),
    而抛异常会逼着每个调用点都写 try. 判不了只有两种原因, 而它们各自的修法不同
    (去升级依赖 / 去装依赖) —— 正好用现成的 `CostGap` 表达, 不必另造一个记号.
    """
    try:
        import chinese_calendar
    except ImportError:
        # ImportError 而不是只认 ModuleNotFoundError: 装坏了 / 被挡掉的依赖走的是
        # 同一个岔口, 而它们给使用者的修法是同一句 (去装/去修依赖)
        return CostGap.NO_CALENDAR_LIB
    try:
        return bool(chinese_calendar.is_workday(day))
    except NotImplementedError:
        return CostGap.NO_CALENDAR_DATA


@dataclass(frozen=True, slots=True)
class CostLine:
    """某一档的用量与金额 (单价随行, 好让展示层把算式原样写出来).

    attributes:
        tier: `cache_miss` / `cache_hit` / `output`.
        tokens: 这一档的 token 数.
        price: 每百万 token 的单价.
        amount: `tokens x price / 百万`, 已量化到 6 位小数.
    """

    tier: str
    tokens: int
    price: Decimal
    amount: Decimal


@dataclass(frozen=True, slots=True)
class RunCost:
    """一次运行折算出来的钱 + 它的来路 (写进运行行的那两列就是它).

    两种结局二选一 (`gap is None` 就是算出来了): 算出来了带三档明细、金额与**当时
    算价的档位**; 算不出来带原因与排查要用的上下文. 明细跟着金额一起落库 ——
    只留一个数字的话, 事后没人能验算, 连「这是峰价还是谷价算的」都看不出来.

    attributes:
        tier: 算价时用的是哪一套价 (`peak` / `valley`); None = 这个模型全天一个价
            (或算不出来).
        lines: 三档明细; 算不出来时是空元组.
        total: 三档金额之和; 算不出来时 None.
        gap: 算不出来的原因; 算出来了就是 None.
        missing: `NO_TOKENS` 时点名缺哪几个分量 (按列的次序).
        derived: 哪几档是**用输入总量减出来的** (见 `_fill_missing_tier`).
        model: 折算用的是哪个模型名; None 表示这行 runs 没记模型名.
        date: 判峰谷时用到的那一天 (`NO_CALENDAR_*` 时说明是哪一年缺数据).
        error: `BAD_CONFIG` 时价目表读不成的原文.
    """

    tier: str | None = None
    lines: tuple[CostLine, ...] = ()
    total: Decimal | None = None
    gap: CostGap | None = None
    missing: tuple[str, ...] = ()
    derived: tuple[str, ...] = ()
    model: str | None = None
    date: str | None = None
    error: str | None = None

    @property
    def known(self) -> bool:
        """算出来了吗 (写路径据此决定动不动那两列)."""
        return self.gap is None

    @property
    def line_of(self) -> Mapping[str, CostLine]:
        """按档名取明细 (展示层按档名找)."""
        return {line.tier: line for line in self.lines}

    def to_detail(self) -> dict[str, Any]:
        """落库的形状 (**唯一一处定义**): 两列里那一列 JSONB 装的就是它.

        金额与单价一律存**字符串**: JSON 的数字是浮点, 而钱不能过浮点 (本仓那条
        「金额用 NUMERIC 不用浮点」在 JSON 这一层同样成立). 键名与读取端
        (`from_detail`) 是一对, 改一处必须改两处 —— 有往返用例钉着.

        Returns:
            dict: 能直接写进 JSONB 的那份载荷; 两种形状由 `kind` 区分
                (`cost` = 有金额, `gap` = 只有原因).
        """
        if not self.known:
            detail: dict[str, Any] = {
                "kind": "gap",
                "reason": self.gap.value if self.gap else "",
            }
            context: dict[str, Any] = {
                "model": self.model,
                "missing": list(self.missing),
                "date": self.date,
                "error": self.error,
            }
            # 只写有值的那些键: 一串 null 会让读的人分不清「没有」与「没记」
            detail.update({key: value for key, value in context.items() if value})
            return detail
        return {
            "kind": "cost",
            "tier": self.tier,
            "total": str(self.total),
            "derived": list(self.derived),
            "lines": [
                {
                    "tier": line.tier,
                    "tokens": line.tokens,
                    "price": str(line.price),
                    "amount": str(line.amount),
                }
                for line in self.lines
            ],
        }

    @classmethod
    def from_detail(cls, detail: Mapping[str, Any] | None) -> RunCost:
        """落库的那份明细 -> RunCost (trace 读库时走这条).

        三种来路分得开 (它们对读的人意思完全不同): 没有明细 (`None` —— 那一趟
        压根没收尾 / 是改口径之前写的) -> `NO_DETAIL`; 有明细但形状不认识 ->
        `UNKNOWN_DETAIL`; 明细里写着「算不出来」-> 原样搬回来.

        Args:
            detail: `to_detail` 的产物; None 或读不懂的形状 -> 给一个带原因的空
                结果 —— 老行与坏数据不该让整段读不出来 (与 `db/conversation.py`
                对坏数据的态度一致).

        Returns:
            RunCost: 读回来的金额与算式, 或者一个带原因的空结果.
        """
        if detail is None:
            return RunCost(gap=CostGap.NO_DETAIL)
        if not isinstance(detail, Mapping):
            return RunCost(gap=CostGap.UNKNOWN_DETAIL)
        try:
            if detail.get("kind") == "cost":
                return cls(
                    tier=detail.get("tier"),
                    total=Decimal(str(detail["total"])),
                    derived=tuple(detail.get("derived") or ()),
                    lines=tuple(
                        CostLine(
                            tier=str(item["tier"]),
                            tokens=int(item["tokens"]),
                            price=Decimal(str(item["price"])),
                            amount=Decimal(str(item["amount"])),
                        )
                        for item in detail.get("lines") or ()
                    ),
                )
            if detail.get("kind") == "gap":
                return cls(
                    gap=CostGap(str(detail.get("reason"))),
                    model=detail.get("model"),
                    missing=tuple(detail.get("missing") or ()),
                    date=detail.get("date"),
                    error=detail.get("error"),
                )
        except (KeyError, TypeError, ValueError, ArithmeticError):
            # ArithmeticError: `Decimal("不是数")` 抛的是它 (DecimalException 那一族)
            return RunCost(gap=CostGap.UNKNOWN_DETAIL)
        return RunCost(gap=CostGap.UNKNOWN_DETAIL)


@dataclass(frozen=True, slots=True)
class PriceTable:
    """模型名 -> 单价 (三档或峰谷两套) + 峰谷规则; 默认空表 = 一个模型都没配价.

    attributes:
        models: 模型名 -> 价. 里面的键要么是 `{"peak", "valley"}` 两套, 要么是
            `{"flat"}` 一套 (配置里写三档就是它, 表示全天一个价).
        rule: 峰谷规则; None = 这份表里没有模型需要判峰谷 (只配了全天一价).
        error: 表**压根没读成**时的原因 (配置写错了) —— 有值时任何定价请求都以它
            为答案. 不在这里抛: 读配置的那一刻由调用方决定「拦下来还是降级」
            (启动自检拦, 记录员降级), 而这里只负责把「没读成」这件事带下去.
    """

    models: Mapping[str, Mapping[str, TierPrices]] = field(default_factory=dict)
    rule: PeakRule | None = None
    error: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PriceTable:
        """从环境变量读价目表 (没配 -> 空表).

        Args:
            env: 环境变量表 (默认读 `os.environ`).

        Returns:
            PriceTable: 解析好的价目表; 变量没设或为空 -> 空表.

        Raises:
            DataConfigError: 价目表不是合法 JSON / 形状不对 / 缺档 / 多档 /
                单价不是数字或是负数 / 用了峰谷价却没给规则 —— 全都在**读配置的
                那一刻**报, 并说清错在哪一条. 不容忍的理由: 静默丢一个模型, 表现
                是「这个运行报没配单价」而配置里明明写着; 缺的那档按 0 算则更糟.
        """
        source = os.environ if env is None else env
        raw = (source.get(ENV_MODEL_PRICES) or "").strip()
        return cls.from_json(raw)

    @classmethod
    def from_json(cls, raw: str) -> PriceTable:
        """价目表文本 (JSON) -> PriceTable; 空串 = 空表."""
        if not raw:
            return cls()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DataConfigError(f"{ENV_MODEL_PRICES} 不是合法 JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise DataConfigError(
                f"{ENV_MODEL_PRICES} 应该是一个 JSON 对象 "
                f"(含 timezone / peak_windows / models 三段), "
                f"实际是 {type(parsed).__name__}: {raw[:60]}"
            )
        known_keys = (_KEY_TIMEZONE, _KEY_PEAK_WINDOWS, _KEY_MODELS)
        unknown = [key for key in parsed if key not in known_keys]
        if unknown:
            raise DataConfigError(
                f"{ENV_MODEL_PRICES} 顶层有不认识的键: {', '.join(map(str, unknown))} "
                f"(只认: {_KEY_TIMEZONE} / {_KEY_PEAK_WINDOWS} / {_KEY_MODELS})"
            )
        models_raw = parsed.get(_KEY_MODELS)
        if models_raw is None:
            raise DataConfigError(
                f"{ENV_MODEL_PRICES} 少了 {_KEY_MODELS} 这一段 (模型名 -> 三档或"
                "峰谷两套单价)"
            )
        if not isinstance(models_raw, dict):
            raise DataConfigError(
                f"{ENV_MODEL_PRICES} 的 {_KEY_MODELS} 应该是一个对象, "
                f"实际是 {type(models_raw).__name__}"
            )
        models = {
            str(model): _model_prices(model, entry)
            for model, entry in models_raw.items()
        }
        needs_rule = [
            model for model, entry in models.items() if PEAK in entry or VALLEY in entry
        ]
        rule = _rule_of(parsed, needs_rule)
        return cls(models=models, rule=rule)

    def tier_needed(self, model: str | None) -> bool:
        """这个模型要不要判峰谷 (全天一价的不需要, 于是也不需要日历).

        Args:
            model: 模型名; None / 表里没有 -> 不需要 (没有价可判).

        Returns:
            bool: 要判峰谷 True (配置里写了两套价).
        """
        entry = self.models.get(model) if model is not None else None
        return bool(entry) and FLAT not in entry

    def price_for(self, model: str | None, tier: str | None) -> TierPrices | None:
        """取一个模型在某一档时段的单价 (没配 / 不缺峰谷都可能是 None, 见实现).

        两种查法合成一处: 全天一价的模型**不看时段** (给它 `tier` 也照样回那一套),
        峰谷表则按时段取 —— 于是调用方不必先问「这个模型分不分钟段」.

        Args:
            model: 模型名; None / 表里没有 -> None.
            tier: 判出来的时段 (`peak` / `valley`); None = 没判 (全天一价的模型
                不看它).

        Returns:
            TierPrices | None: 那套三档单价; None = 这个模型没配 (或这一档没配).
        """
        entry = self.models.get(model) if model is not None else None
        if not entry:
            return None
        if FLAT in entry:
            return entry[FLAT]
        return entry.get(tier) if tier is not None else None


def cost_of(
    *,
    model: str | None,
    table: PriceTable,
    moment: datetime | None,
    input_tokens: int | None,
    cache_miss_tokens: int | None,
    cache_hit_tokens: int | None,
    output_tokens: int | None,
) -> RunCost:
    """一次运行的用量 + 开始时刻 + 模型名 -> 金额 (含「为什么算不出来」).

    **收 `input_tokens` 却不拿它定价**: 定价只看三档, 但输入总量是拆分的一份佐证
    —— 上游没报「未命中」那一档时 (OpenAI 系只给 `prompt_tokens_details.cached_tokens`,
    不给未命中数), 未命中 = 输入总量 - 命中. 两个数都是上游报的, 减出来的那一档
    因此仍是**事实**; 减出来是负数就地放弃 (那是上游数据不对).

    `reasoning_tokens` 与 `total_tokens` 刻意**没有参数位**: 前者是输出档的明细
    (计价会重复), 后者是三类不同价 token 的混合 (拿它乘等于把三档的价格关系抹平).

    Args:
        model: 这次运行用的模型名 (runs.model); None = 没记, 查不了价.
        table: 价目表.
        moment: 这次运行的**开始时刻** (runs.created_at, 带时区) —— 判峰谷的输入.
        input_tokens: 输入总量; 只用来补算缺的那一档缓存分量.
        cache_miss_tokens / cache_hit_tokens / output_tokens: 三档的累计用量.
            None = 上游一次都没上报过这一档 —— 与 0 (报过、值就是零) 是两回事.

    Returns:
        RunCost: 算出来了 (档位 + 三档明细 + 合计), 或者带原因的空结果.
    """
    if table.error is not None:
        return RunCost(gap=CostGap.BAD_CONFIG, error=table.error, model=model)
    if model is None or model not in table.models:
        return RunCost(gap=CostGap.NO_PRICE, model=model)

    tier: str | None = None
    if table.tier_needed(model):
        rule = table.rule
        if rule is None:
            # 解析期就拦了 (用了峰谷价却没给规则), 走到这里说明表是手搓的 ——
            # 如实报「配置不成套」, 不猜一套规则出来
            return RunCost(
                gap=CostGap.BAD_CONFIG,
                error=f"{model} 配了峰谷两套价, 但没有峰谷规则",
                model=model,
            )
        verdict = rule.tier_at(moment)
        if verdict.tier is None:
            return RunCost(gap=verdict.gap, date=verdict.date, model=model)
        tier = verdict.tier

    prices = table.price_for(model, tier)
    if prices is None:
        # 峰谷表缺一套 (解析期该拦住) —— 与上面的不成套同一种性质
        return RunCost(
            gap=CostGap.BAD_CONFIG,
            error=f"{model} 没有 {tier} 这一套单价",
            model=model,
        )

    miss = _fill_missing_tier(cache_miss_tokens, cache_hit_tokens, input_tokens)
    hit = _fill_missing_tier(cache_hit_tokens, cache_miss_tokens, input_tokens)
    tokens = {"cache_miss": miss, "cache_hit": hit, "output": output_tokens}
    missing = tuple(_COMPONENT_OF_TIER[name] for name in _TIERS if tokens[name] is None)
    if missing:
        return RunCost(gap=CostGap.NO_TOKENS, missing=missing, model=model, tier=tier)

    lines = tuple(_line(name, tokens[name], getattr(prices, name)) for name in _TIERS)
    # 哪几档是减出来的 (原来那一列是空的) —— 展示层要据此标明来路
    derived = tuple(
        name
        for name, reported, filled in (
            ("cache_miss_tokens", cache_miss_tokens, miss),
            ("cache_hit_tokens", cache_hit_tokens, hit),
        )
        if reported is None and filled is not None
    )
    total = sum((line.amount for line in lines), Decimal(0))
    if total == 0:
        # 三档的**原始**金额加起来还是正数, 但每档都小于 1e-6 (这一列的最小刻度):
        # 写进去就是 0.000000 —— 而 0 在这一列里的意思是「真的没花钱」, 那是撒谎.
        # 金额小到这种程度的运行现实中不会出现 (几个 token 也值 1e-7 以上), 但
        # 「算不出来」与「算出来是零」必须分得开, 所以这里如实报「存不下」
        raw = sum(
            (Decimal(tokens[name]) * getattr(prices, name) for name in _TIERS),
            Decimal(0),
        )
        if raw > 0:
            return RunCost(gap=CostGap.TOO_SMALL, model=model, tier=tier)
    return RunCost(
        tier=tier,
        lines=lines,
        total=total,
        derived=derived,
        model=model,
    )


def ensure_pricing_ready(table: PriceTable, *, moment: datetime | None = None) -> None:
    """启动自检: 这份价目表此刻拿得出手吗 (拿不出就抛, 由调用方拒绝启动).

    查两件: 表本身读成了没; 若表里有峰谷价, **今天这一刻判得了峰谷吗**
    (日历数据过没过期 / 依赖装没装). 只配全天一价的部署直接通过 —— 它们不需要
    日历 (「不用的东西不该拦住启动」).

    Args:
        table: 读出来的价目表.
        moment: 拿哪一刻做自检 (默认当下); 测试用它固定时间.

    Raises:
        PricingNotReadyError: 表没读成, 或今天判不了峰谷.
    """
    if table.error is not None:
        raise PricingNotReadyError(f"价目表没读成: {table.error}")
    if not table.models or table.rule is None:
        return
    verdict = table.rule.tier_at(moment if moment is not None else datetime.now(UTC))
    if verdict.tier is None:
        raise PricingNotReadyError(_readiness_text(verdict))


def load_pricing(env: Mapping[str, str] | None = None) -> PriceTable:
    """读价目表 + 启动自检 (启动那一步的**唯一入口**: 两条路都走它).

    与 `ensure_pricing_ready` 的分工: 这个「读进来再查」, 那个「查一份现成的表」
    (记录员拿到注入的价目表时走后者). 读配置时报的 `DataConfigError` 在这里被
    翻成 `PricingNotReadyError` —— 两种失败对调用方是同一件事 (去修配置 / 去升
    依赖), 而业务侧的启动错误列表只认后者这一类.

    Args:
        env: 环境变量表 (默认读 `os.environ`); 测试用它固定配置.

    Returns:
        PriceTable: 读好且通过自检的价目表 (可能是空表 —— 没配价不是错误).

    Raises:
        PricingNotReadyError: 配置写错, 或配了峰谷价却判不了今天.
    """
    try:
        table = PriceTable.from_env(env)
    except DataConfigError as exc:
        raise PricingNotReadyError(f"价目表没读成: {exc}") from exc
    ensure_pricing_ready(table)
    return table


def _readiness_text(verdict: TierVerdict) -> str:
    """判不了峰谷时的启动报错文案 (能照着做的那种)."""
    if verdict.gap is CostGap.NO_CALENDAR_LIB:
        return (
            "配了峰谷价目表, 但没装中国节假日日历: "
            'pip install "charagent[pricing]" (或 pip install chinesecalendar)'
        )
    return (
        f"峰谷价目表要按中国工作日判峰谷, 但日历里没有 {verdict.date} 这一天所在的"
        "年份 —— 升级依赖即可: pip install -U chinesecalendar"
    )


def _fill_missing_tier(
    wanted: int | None,
    other: int | None,
    total: int | None,
) -> int | None:
    """补齐输入侧缺的那一档缓存分量 (`wanted = total - other`), 补不出来给 None.

    只在**真的缺**的时候补, 且只在两个减数都拿得到的时候补:

    - 上游报了 `input_tokens` 与另一档缓存分量时, 这一档就是它们的差 —— 上游自己
      的数字就满足这条 (实测五组样本都是 `命中 + 未命中 = 输入`).
    - **差是负数就地放弃** (报了缓存命中比输入总量还多 —— 那是上游数据不对): 负的
      token 数按缺处理, 让上层报「算不出来」, 而不是算出一个负金额.
    - 两个减数缺一个就补不出来 (None), 上层照旧报「缺这一档」.

    Returns:
        int | None: 补齐后的用量; None = 补不出来 (还是缺).
    """
    if wanted is not None or other is None or total is None:
        return wanted
    derived = total - other
    return derived if derived >= 0 else None


def _line(tier: str, tokens: int, price: Decimal) -> CostLine:
    """一档的用量与单价 -> 金额 (量化到 6 位, 与那一列同标度).

    Returns:
        CostLine: 这一档的用量 / 单价 / 金额.
    """
    amount = (Decimal(tokens) * price / TOKENS_PER_PRICE_UNIT).quantize(
        _AMOUNT_SCALE, rounding=ROUND_HALF_UP
    )
    return CostLine(tier=tier, tokens=tokens, price=price, amount=amount)


# ---------------------------------------------------------------------------
# 解析配置 (读一次, 错一条就报一条)
# ---------------------------------------------------------------------------


def _model_prices(model: str, entry: object) -> Mapping[str, TierPrices]:
    """一个模型的价 -> {peak/valley 两套} 或 {flat 一套} (形状不对当场报错).

    Returns:
        Mapping[str, TierPrices]: 键要么是 `{"peak", "valley"}`, 要么是 `{"flat"}`.

    Raises:
        DataConfigError: 不是对象 / 三档与峰谷混在一层 / 缺一套峰谷 / 多键 ->
            再往下由 `_tier_prices` 接着报 (缺档 / 非数字 / 负数).
    """
    if not isinstance(entry, dict):
        raise DataConfigError(
            f"价目表里 {model!r} 的单价应该是一个对象 "
            f'(三档: {{"cache_miss": 2, "cache_hit": 0.5, "output": 8}}; '
            f'或峰谷两套: {{"peak": {{...}}, "valley": {{...}}}}), '
            f"实际是 {type(entry).__name__}: {entry!r}"
        )
    has_split = PEAK in entry or VALLEY in entry
    has_flat = any(tier in entry for tier in _TIERS)
    if has_split and has_flat:
        raise DataConfigError(
            f"价目表里 {model!r} 把三档与 peak/valley 混在一层了 —— 一个模型要么"
            "写三档 (全天一个价), 要么写 peak + valley 两套"
        )
    if has_split:
        missing = [name for name in (PEAK, VALLEY) if name not in entry]
        if missing:
            raise DataConfigError(
                f"价目表里 {model!r} 少了 {', '.join(missing)} 这一套 —— 峰谷价必须"
                "两套都给 (只给一套等于让另一套用同一个价, 猜不如写全)"
            )
        unknown = [key for key in entry if key not in (PEAK, VALLEY)]
        if unknown:
            raise DataConfigError(
                f"价目表里 {model!r} 有不认识的键: {', '.join(map(str, unknown))} "
                f"(分峰谷时只认: {PEAK} / {VALLEY})"
            )
        return {name: _tier_prices(model, name, entry[name]) for name in (PEAK, VALLEY)}
    return {FLAT: _tier_prices(model, FLAT, entry)}


def _tier_prices(model: str, bracket: str, entry: object) -> TierPrices:
    """一套三档单价 -> TierPrices (缺档 / 多键 / 非数字 / 负数都当场报错)."""
    where = f"{model!r}" if bracket == FLAT else f"{model!r} 的 {bracket}"
    if not isinstance(entry, dict):
        raise DataConfigError(
            f"价目表里 {where} 应该是一个对象 (三档单价), "
            f"实际是 {type(entry).__name__}: {entry!r}"
        )
    missing = [tier for tier in _TIERS if tier not in entry]
    if missing:
        raise DataConfigError(
            f"价目表里 {where} 少了这些档: {', '.join(missing)} "
            f"(三档都要: {', '.join(_TIERS)}) —— 缺的那档按 0 算会让金额偏低"
        )
    unknown = [key for key in entry if key not in _TIERS]
    if unknown:
        raise DataConfigError(
            f"价目表里 {where} 有不认识的档: {', '.join(map(str, unknown))} "
            f"(只认: {', '.join(_TIERS)}) —— 写错档名等于那一档没配"
        )
    return TierPrices(
        cache_miss=_as_price(where, "cache_miss", entry["cache_miss"]),
        cache_hit=_as_price(where, "cache_hit", entry["cache_hit"]),
        output=_as_price(where, "output", entry["output"]),
    )


def _as_price(where: str, tier: str, value: object) -> Decimal:
    """一档的单价 -> Decimal (只收数字: 字符串与布尔都不是钱).

    用 `Decimal(str(value))` 而不是 `Decimal(value)`: JSON 解出来的是 float, 而
    `Decimal(0.5)` 会带上一串二进制小数的尾巴; 走一遍字符串得到的就是人写的那个数.

    Args:
        where: 报错文案里「哪儿」的那一段 (模型名, 或模型名 + 哪一套).
        tier: 哪一档 (报错文案里要用).
        value: JSON 里那个值.

    Returns:
        Decimal: 这一档的单价.

    Raises:
        DataConfigError: 不是数字 (含布尔) 或者是负数.
    """
    # bool 是 int 的子类, 不单独挡掉的话 `true` 会被当成 1
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DataConfigError(
            f"价目表里 {where} 的 {tier} 不是数字: {value!r} "
            "(单位是每百万 token 的金额, 如 2 或 0.5)"
        )
    price = Decimal(str(value))
    if price < 0:
        raise DataConfigError(
            f"价目表里 {where} 的 {tier} 是负数: {value!r} (单价不能是负的)"
        )
    return price


def _rule_of(parsed: Mapping[str, Any], needs_rule: list[str]) -> PeakRule | None:
    """配置里那两段 -> PeakRule; 没有模型需要它时返回 None.

    Args:
        parsed: 顶层 JSON 对象.
        needs_rule: 哪些模型写了 peak/valley (有它就必须要规则).

    Raises:
        DataConfigError: 该给规则却没给 / 时区名不认识 / 峰窗写错.
    """
    zone = parsed.get(_KEY_TIMEZONE)
    windows_raw = parsed.get(_KEY_PEAK_WINDOWS)
    if not needs_rule:
        return None
    if zone is None or windows_raw is None:
        missing = []
        if zone is None:
            missing.append(_KEY_TIMEZONE)
        if windows_raw is None:
            missing.append(_KEY_PEAK_WINDOWS)
        raise DataConfigError(
            f"{', '.join(needs_rule[:3])} 这些模型写了 peak/valley 两套价, 但配置里"
            f"少了 {', '.join(missing)} —— 不给规则就会拿一套默认规则静默算错钱, "
            "那比报错难查得多"
        )
    if not isinstance(zone, str):
        raise DataConfigError(f'{_KEY_TIMEZONE} 应该是时区名 (如 "Asia/Shanghai")')
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DataConfigError(
            f"{_KEY_TIMEZONE} 不是一个认识的时区名: {zone!r} (用 IANA 名, "
            '如 "Asia/Shanghai")'
        ) from exc
    return PeakRule(timezone=zone, windows=_windows_of(windows_raw))


def _windows_of(raw: object) -> tuple[tuple[time, time], ...]:
    """峰窗 -> ((start, end), ...); 半开区间 `[start, end)`.

    Raises:
        DataConfigError: 不是两张的列表 / 时刻写错 / 起止颠倒或相等 / 一个都没有.
    """
    if not isinstance(raw, list) or not raw:
        raise DataConfigError(
            f"{_KEY_PEAK_WINDOWS} 应该是一个非空的列表, 如 "
            '[["09:00", "12:00"], ["14:00", "18:00"]]'
        )
    windows: list[tuple[time, time]] = []
    for item in raw:
        if not isinstance(item, list | tuple) or len(item) != 2:
            raise DataConfigError(
                f"{_KEY_PEAK_WINDOWS} 里每一条应该是 [起, 止] 两项, 实际: {item!r}"
            )
        start, end = (_as_clock(value) for value in item)
        if start >= end:
            raise DataConfigError(
                f"{_KEY_PEAK_WINDOWS} 里 {item!r} 的起止不对 (起必须早于止)"
            )
        windows.append((start, end))
    return tuple(windows)


def _as_clock(value: object) -> time:
    """`"09:00"` -> time (只认时:分, 不认秒与任意字符串).

    Raises:
        DataConfigError: 不是 `HH:MM` 形状.
    """
    text = str(value)
    parts = text.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise DataConfigError(
            f'{_KEY_PEAK_WINDOWS} 里的时刻要写成 "HH:MM" (如 "09:00"), 实际: {value!r}'
        )
    hour, minute = (int(part) for part in parts)
    if hour > 23 or minute > 59:
        raise DataConfigError(f"{_KEY_PEAK_WINDOWS} 里的时刻超出范围: {value!r}")
    return time(hour, minute)
