"""演示工具集 (P0-2): 验证 @tool 双引擎 + 供 P0-3 loop 联调 / P0-10 CLI 复用.

工具定义按生产 SOTA 写法 (OpenAI function calling 官方实践 / Pydantic AI 同款):
- 工具名动词短语 snake_case; docstring 首段说明「何时用 / 不用 + 返回内容」
- 每参数签名级 Annotated + Field: description 说清格式期望 (填参正确率,
  #10/#68), 约束即类型 (pattern / gt / Literal / StrEnum), examples 示例值
- 业务规则不入 schema 时 → 函数内 raise ToolActionableError (#2)

本集 6 个工具均业务无关、无外部依赖、确定性输出:
    1-5  pydantic 引擎 (SOTA): get_current_time / convert_length /
          batch_convert_lengths / count_text_stats / query_order_status
    6    manual 引擎 (教学对照, @tool(schema="manual")): 与 #5 同能力的
          query_order_status_manual —— 手写 typing 映射 + docstring Args 描述
          + 函数内手动校验, 对照「pydantic 自动约束 vs 手写映射 + 手写校验」.
真实客服业务工具 (订单/物流/退款/FAQ/转人工) 属 P1-13 demo 层, 不在本包.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from CharAgent.tool.decorator import tool
from CharAgent.tool.utils.errors import ToolActionableError

# ---------------------------------------------------------------------------
# 工具 1: 当前时间 (Literal 枚举 + 默认值 + examples)
# ---------------------------------------------------------------------------

# 演示用固定偏移时区表 (真实生产请用 zoneinfo 数据库 / tz 依赖)
_TZ_OFFSETS: dict[str, timezone] = {
    "utc": UTC,
    "shanghai": timezone(timedelta(hours=8), name="CST"),
}


@tool
def get_current_time(
    fmt: Annotated[
        Literal["iso", "date", "time"],
        Field(
            description="返回格式: iso 为完整日期时间含时区, date 仅日期, "
            "time 仅时分秒",
            examples=["iso"],
        ),
    ] = "iso",
    timezone: Annotated[
        Literal["local", "utc", "shanghai"],
        Field(
            description="时区: local 为服务器本机时区, utc 为世界协调时, "
            "shanghai 为北京时间 (UTC+8)",
            examples=["utc"],
        ),
    ] = "local",
) -> str:
    """获取当前日期时间. 用户问「现在几点 / 今天日期」等时间类问题, 或需要
    时间戳核对时使用; 非时间类问题不要调用. 返回按格式与时区格式化后的文本.
    """
    tz = _TZ_OFFSETS.get(timezone)
    now = datetime.now(tz) if tz is not None else datetime.now().astimezone()
    if fmt == "date":
        return now.strftime("%Y-%m-%d")
    if fmt == "time":
        return now.strftime("%H:%M:%S")
    return now.strftime("%Y-%m-%d %H:%M:%S %z")


# ---------------------------------------------------------------------------
# 工具 2: 长度单位换算 (数值约束 + 枚举单位)
# ---------------------------------------------------------------------------


class LengthUnit(StrEnum):
    # 注意: pydantic 会把枚举类的 docstring 注入字段 description (覆盖 Field 描述),
    # 故枚举不写 docstring, 描述统一放字段 Field(description=...) 上
    METER = "meter"
    CENTIMETER = "centimeter"
    KILOMETER = "kilometer"
    MILE = "mile"
    FOOT = "foot"
    INCH = "inch"


_TO_METERS: dict[LengthUnit, float] = {
    LengthUnit.METER: 1.0,
    LengthUnit.CENTIMETER: 0.01,
    LengthUnit.KILOMETER: 1000.0,
    LengthUnit.MILE: 1609.344,
    LengthUnit.FOOT: 0.3048,
    LengthUnit.INCH: 0.0254,
}


@tool
def convert_length(
    value: Annotated[
        float,
        Field(description="待换算的长度数值 (必须大于 0)", gt=0, examples=[3.5]),
    ],
    from_unit: Annotated[
        LengthUnit,
        Field(description="源单位", examples=["kilometer"]),
    ],
    to_unit: Annotated[
        LengthUnit,
        Field(description="目标单位", examples=["mile"]),
    ],
) -> str:
    """长度单位换算. 用户给出带长度单位的数值并要求换算为另一单位时使用;
    支持 meter/centimeter/kilometer/mile/foot/inch. 返回换算结果文本.
    """
    meters = value * _TO_METERS[from_unit]
    converted = meters / _TO_METERS[to_unit]
    return f"{value:g} {from_unit.value} = {converted:g} {to_unit.value}"


# ---------------------------------------------------------------------------
# 工具 3: 批量长度换算 (list[嵌套模型] → $defs 展开, #70 嵌套 dict)
# ---------------------------------------------------------------------------


class LengthInput(BaseModel):
    """批量换算的单条输入: 数值 + 原单位."""

    value: float = Field(description="长度数值 (必须大于 0)", gt=0, examples=[10])
    unit: LengthUnit = Field(description="原单位", examples=["kilometer"])


@tool
def batch_convert_lengths(
    items: Annotated[
        list[LengthInput],
        Field(description="待换算的长度列表 (非空)", min_length=1),
    ],
    to_unit: Annotated[
        LengthUnit,
        Field(description="统一换算的目标单位", examples=["foot"]),
    ],
    precision: Annotated[
        int,
        Field(
            description="结果保留的小数位 (0-6), 默认 2",
            ge=0,
            le=6,
            examples=[2],
        ),
    ] = 2,
) -> str:
    """批量长度单位换算. 一次传入多条长度 (数值+单位) 并统一换算到目标单位时
    使用; 返回换算结果 JSON 文本 (字段: items[].value/unit/converted/to_unit).
    """
    results: list[dict[str, Any]] = []
    for item in items:
        meters = item.value * _TO_METERS[item.unit]
        converted = meters / _TO_METERS[to_unit]
        results.append(
            {
                "value": item.value,
                "unit": item.unit.value,
                "converted": round(converted, precision),
                "to_unit": to_unit.value,
            }
        )
    return json.dumps({"items": results}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 工具 4: 文本统计 (必填 str + Literal + bool 默认值)
# ---------------------------------------------------------------------------


@tool
def count_text_stats(
    text: Annotated[
        str,
        Field(
            description="待统计的文本 (非空)",
            min_length=1,
            examples=["你好, CharAgent!"],
        ),
    ],
    mode: Annotated[
        Literal["chars", "words", "lines"],
        Field(
            description="统计维度: chars 字符数, words 以空白分词后的词数, "
            "lines 换行分段数",
            examples=["chars"],
        ),
    ],
    ignore_whitespace: Annotated[
        bool,
        Field(
            description="chars 维度是否忽略空白字符 (空格/换行), 其他维度无效",
            examples=[True],
        ),
    ] = True,
) -> str:
    """统计文本的长度 (字符/词/行). 用户询问一段文字的字符数、词数或行数时
    使用. 返回对应维度的整数文本.
    """
    if mode == "lines":
        return f"行数: {len(text.splitlines())}"
    if mode == "words":
        return f"词数: {len(text.split())}"
    if ignore_whitespace:
        text = re.sub(r"\s+", "", text)
    return f"字符数: {len(text)}"


# ---------------------------------------------------------------------------
# 工具 5: 订单状态查询 (pydantic pattern 约束 + mock 数据)
# ---------------------------------------------------------------------------

# 演示 mock 数据 (固定订单表; 真实客服查询属 P1-13 demo 层接入数据源, 形态不变)
MOCK_ORDERS: dict[str, str] = {
    "20260701123456": "已发货, 预计 2026-07-05 送达 (承运: 顺丰 SF7890123456)",
    "20260702098765": "待发货, 预计 2026-07-08 送达",
    "20260630024680": "已签收 (签收时间 2026-07-03 14:20)",
}


@tool
def query_order_status(
    order_no: Annotated[
        str,
        Field(
            description="14 位数字订单号",
            pattern=r"^\d{14}$",
            examples=["20260701123456"],
        ),
    ],
) -> str:
    """查询订单状态与物流概要. 用户报出订单号并询问发货 / 物流状态时使用;
    未提供订单号时应先向用户索要, 不要猜测订单号. 返回状态文本,
    查无此单时返回提示信息.
    """
    status = MOCK_ORDERS.get(order_no)
    if status is None:
        return f"未查询到订单 {order_no} 的记录, 请与用户核对订单号"
    return f"订单 {order_no}: {status}"


# ---------------------------------------------------------------------------
# 工具 6: 订单状态查询 manual 对照实现 (@tool(schema="manual"))
#
# 与工具 5 同能力 (同一 mock 表, 输出一致), 但 schema 由 manual 引擎生成:
#   纯类型注解 + Google docstring Args 描述; 无自动参数校验 → 格式校验
#   (14 位订单号) 由函数内 raise ToolActionableError 承担 (#2 正解).
# 对比教学点: pydantic 的 Field(pattern) 把规则送进 schema (模型填参即被约束)
# vs manual 的运行时校验 (模型填错后收到可操作错误再自纠错).
# ---------------------------------------------------------------------------


@tool(schema="manual")
def query_order_status_manual(order_no: str) -> str:
    """查询订单状态与物流概要 (manual 引擎对照实现, 能力与 query_order_status
    相同). 用户报出订单号并询问发货 / 物流状态时使用. 返回状态文本,
    查无此单时返回提示信息.

    Args:
        order_no: 14 位数字订单号.
    """
    if not re.fullmatch(r"\d{14}", order_no):
        raise ToolActionableError(
            f"order_no 应为 14 位数字, 实际 {order_no!r}, 请核对后重试"
        )
    status = MOCK_ORDERS.get(order_no)
    if status is None:
        return f"未查询到订单 {order_no} 的记录, 请与用户核对订单号"
    return f"订单 {order_no}: {status}"
