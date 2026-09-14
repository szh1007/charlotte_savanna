"""映射零件的自检 (不需要数据库): 实体 → 字典 / SQL 参数的转换.

`repositories/utils/mapping.py` 是**搬运工**: 把实体摊平成字典 (给日志、测试断言、
接口返回用), 或者摊平成 SQL 参数 (写入用). 它不做校验也不做业务判断 —— 这组用例
守的正是这条边界: 转换该**忠实** (值别被悄悄改写), 该**可预期** (哪些类型会变
成字符串是列得清的).

为什么值得单独测: 转换里加一点「顺手处理」看着无害, 但会让「存进去的值」与
「读出来的值」分家 —— 而这类 bug 只在某个特定类型上出现, 极难回溯.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

from CharAgent.db.repositories.utils.mapping import (
    entity_params,
    jsonable,
    model_to_dict,
)


class _Base(DeclarativeBase):
    """本文件专用的映射基类 (不碰真正的五张表)."""


_probe_table = Table(
    "probe_mapping",
    _Base.metadata,
    Column("name", String(32), primary_key=True),
    Column("count", Integer),
    Column("big", BigInteger),
    Column("amount", Numeric(14, 6)),
    Column("note", Text),
    Column("payload", JSONB),
    Column("moment", DateTime(timezone=True)),
)


class _Probe(_Base):
    """一张探针表: 覆盖本包会用到的各种列类型."""

    __table__ = _probe_table


def _sample() -> _Probe:
    """造一行样本 (含各种类型的值).

    金额传 `Decimal` 而不是 float: 从库里读回来的 NUMERIC 就是 Decimal, 样本要
    跟真实读回来的形状一致, 否则测的是「一个线上不存在的输入」.
    """
    return _Probe(
        name="t-1",
        count=3,
        big=2**40,
        amount=Decimal("128.500000"),
        note="备注",
        payload={"keys": ["a", "b"]},
        moment=datetime(2026, 9, 14, 10, 0, tzinfo=UTC),
    )


def test_model_to_dict_keeps_every_column_in_order():
    """字典的键与顺序 = 表里的列 (摊平之后仍然看得出是哪张表)."""
    snapshot = model_to_dict(_sample())

    assert list(snapshot) == [
        "name",
        "count",
        "big",
        "amount",
        "note",
        "payload",
        "moment",
    ]


def test_model_to_dict_turns_datetime_into_iso_text():
    """datetime 变成 ISO 文本 —— 这是给「JSON 能装」用的那一手.

    留着 datetime 对象的话, `json.dumps` 会直接抛异常 (尽管它是合法 Python 值);
    而接口返回、日志、快照对比都需要能序列化.
    """
    snapshot = model_to_dict(_sample())

    assert snapshot["moment"] == "2026-09-14T10:00:00+00:00"
    assert isinstance(snapshot["moment"], str)


def test_timezone_offset_survives_the_conversion():
    """非 UTC 的时区也照原样写进文本 (不悄悄改成 UTC).

    换算成 UTC 看着「更规范」, 但会让「本地时间写的 10 点」在日志里变成别的
    数字 —— 排查时按本地时间找记录的人会找不到. ISO 文本本来就能表达时区.
    """
    shanghai = timezone(timedelta(hours=8))
    row = _Probe(name="t", moment=datetime(2026, 9, 14, 18, 0, tzinfo=shanghai))

    assert model_to_dict(row)["moment"] == "2026-09-14T18:00:00+08:00"


def test_model_to_dict_keeps_non_datetime_values_alone():
    """其余类型原样保留 —— 不做任何「顺手处理」.

    金额那一列要特别留意: SQLAlchemy 会在赋值时把它变成 `Decimal` (那是列类型的
    bind 处理, 不是本文件干的), 而**本文件绝不把它转成 float** —— 金额用浮点
    等于随时可能丢分.
    """
    snapshot = model_to_dict(_sample())

    assert snapshot["count"] == 3
    assert snapshot["big"] == 2**40
    assert isinstance(snapshot["amount"], Decimal), (
        "金额被转成了非 Decimal (浮点算钱会丢分)"
    )
    assert snapshot["payload"] == {"keys": ["a", "b"]}


def test_jsonable_handles_plain_dates_too():
    """`date` 也能转 (有些列只存日期, 没有时刻)."""
    assert jsonable(date(2026, 9, 14)) == "2026-09-14"


def test_jsonable_passes_through_what_json_already_accepts():
    """JSON 本来就装得下的值一律原样返回 (转换只发生在需要它的类型上)."""
    for value in ("文本", 3, 2.5, True, None, ["a"], {"b": 1}):
        assert jsonable(value) is value or jsonable(value) == value


def test_jsonable_does_not_stringify_unknown_objects():
    """遇到不认识的类型**不**转成 `str(...)` —— 那会写出谁也读不懂的东西.

    `str(对象)` 的结果是 `<__main__.Foo object at 0x7f...>`, 写进日志或接口之后
    既没信息量又占位置. 原样返回, 让上层在真正需要时自己处理.
    """

    class Opaque:
        pass

    value = Opaque()

    assert jsonable(value) is value


def test_entity_params_keeps_values_as_objects_for_the_driver():
    """SQL 参数那条路**不转换** —— datetime 还是 datetime.

    为什么两条路不同: SQLAlchemy 知道怎么把 datetime / dict / Decimal 交给驱动;
    提前转成文本反而让时间列收到字符串 (Postgres 能接受, 但时区与类型就模糊了).
    """
    params = entity_params(_sample())

    assert isinstance(params["moment"], datetime)
    assert params["payload"] == {"keys": ["a", "b"]}
    assert params["name"] == "t-1"


def test_both_views_cover_the_same_columns():
    """两条路看到的列完全一样 (一个不少、一个不多)."""
    row = _sample()

    assert set(model_to_dict(row)) == set(entity_params(row))
