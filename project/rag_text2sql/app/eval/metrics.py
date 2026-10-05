"""评估判据: L1 计分 / 极差 / EEX 结果比对 —— 全是纯函数, 不碰真服务.

口径 (与报告头部、README §7 的写法一致):
- 列精确率 / 召回率 **逐题算再取平均** (macro). 30~50 题的规模上 micro 会被
  列多的题主导, 而每题等权才反映"题做不做得出".
- 候选集取自**节点输出** (filter_table / filter_metric 之后的表/列/指标集合),
  不是最终 SQL 的文本 —— 这样"召回错"与"生成错"在报告里分得开.
- 表必命中率 = gold 表集合**全部**出现在候选里 (查全); 指标命中率同理.
  无指标题 (gold_metric 为空) 不参与指标命中率, 记 None 而不是 False.
- EEX (结果完全一致) 忽略列序与行序, 数值保留两位小数 —— 浮点噪声不参与判定.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class L1Outcome:
    """一题的 L1 结果 (某一次运行)."""

    column_precision: float
    column_recall: float
    table_hit: bool
    metric_hit: bool | None


@dataclass(frozen=True)
class Spread:
    """一组数的均值与极差. `rng` 是**臂内极差** —— 一次跑出来的差不能当结论."""

    mean: float
    min: float
    max: float

    @property
    def rng(self) -> float:
        return self.max - self.min


def score_l1(
    *,
    gold_tables: Iterable[str],
    gold_columns: Iterable[str],
    gold_metric: str | None,
    retrieved_tables: Iterable[str],
    retrieved_columns: Iterable[str],
    retrieved_metrics: Iterable[str],
) -> L1Outcome:
    """对一次运行的候选集计分. gold_columns 非空由题库加载期保证."""
    gold_columns = set(gold_columns)
    retrieved_columns = set(retrieved_columns)
    hit = gold_columns & retrieved_columns

    return L1Outcome(
        column_precision=len(hit) / len(retrieved_columns)
        if retrieved_columns
        else 0.0,
        column_recall=len(hit) / len(gold_columns) if gold_columns else 0.0,
        table_hit=set(gold_tables) <= set(retrieved_tables),
        metric_hit=None
        if gold_metric is None
        else gold_metric in set(retrieved_metrics),
    )


def spread(values: Sequence[float]) -> Spread:
    """均值 / 最小值 / 最大值; 空序列是调用方的错, 当场炸."""
    if not values:
        raise ValueError("极差至少要有一个数")

    return Spread(mean=sum(values) / len(values), min=min(values), max=max(values))


def _cell(value: Any) -> str:
    """把一个结果值归一化成可比较的字符串."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float | Decimal):
        return f"{round(float(value), 2):.2f}"
    return str(value)


def normalize_rows(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, ...]]:
    """行内按值排序、行间再排序 —— 列序与行序都不参与比较."""
    return sorted(tuple(sorted(_cell(value) for value in row.values())) for row in rows)


def results_equal(
    left: Iterable[Mapping[str, Any]], right: Iterable[Mapping[str, Any]]
) -> bool:
    """EEX 判据: 两份结果集 (忽略列序/行序/浮点尾差) 是否一致."""
    return normalize_rows(left) == normalize_rows(right)
