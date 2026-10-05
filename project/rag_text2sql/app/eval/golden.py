"""Golden set: 人工写问题 + 人工确认口径 (D4 的结论).

⚠️ 绝不能用「现在跑通的 SQL 反推」当标准答案 —— 那等于把当前实现的缺陷固化成
"正确答案", 题库就永远测不出问题. 题库的全部价值在于它**独立于实现**:
gold 表 / 列 / SQL 是照 `conf/meta_config.yaml` 的建模与 dw 数据语义手写的,
不抄系统输出.

每题记: 问句 / 类别 / gold 表列指标 / gold SQL / 口径说明. `eex=True` 的题
(能手算的那几道) 才参与「结果完全一致 (EEX)」比对; 其余题 gold_sql 的作用是
把口径写死, 供人工核对.

题是**数据**, 落在 `golden_set.yaml` (根 .gitignore 的 `*.yaml` 有它的例外).
加载期严格校验: 未知键 (拼错 `gold_cols` 这类) / 重复 id / 类别不在枚举 /
gold 列不是「表名.列名」—— 都在这里当场炸掉, 而不是变成报告里一个看不懂的数字.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.yaml"

# 覆盖面 (对齐 README §7.2 的建议, 歧义题经 2026-10-05 决定不做, 见 README §7)
CATEGORIES = (
    "single_table",  # 单表聚合
    "join",  # 多表 JOIN 聚合
    "time_range",  # 绝对时间区间
    "relative_time",  # 相对时间 (去年 / 今年 / 上个月)
    "value_filter",  # 维度取值过滤
    "metric_alias",  # 指标别名问法
    "no_metric",  # 无指标列表型问题
)

_REQUIRED_KEYS = (
    "id",
    "category",
    "question",
    "gold_tables",
    "gold_columns",
    "gold_sql",
    "notes",
)
_OPTIONAL_KEYS = ("gold_metric", "eex")


class GoldenSetError(ValueError):
    """题库格式错 —— 加载期炸掉, 不让它变成一个看不懂的数字."""


@dataclass(frozen=True)
class GoldenCase:
    id: str
    category: str
    question: str
    gold_tables: tuple[str, ...]
    gold_columns: tuple[str, ...]
    gold_metric: str | None
    gold_sql: str
    eex: bool
    notes: str


def _fail(where: str, message: str) -> None:
    raise GoldenSetError(f"{where}: {message}")


def _as_case(item: object, index: int) -> GoldenCase:
    if not isinstance(item, dict):
        _fail(f"第 {index} 条", f"应当是映射, 实际是 {type(item).__name__}")

    where = f"第 {index} 条 (id={item.get('id', '?')})"
    unknown = set(item) - set(_REQUIRED_KEYS) - set(_OPTIONAL_KEYS)
    if unknown:
        _fail(where, f"有未知字段 {sorted(unknown)}; 允许的字段: {_REQUIRED_KEYS}")
    missing = [key for key in _REQUIRED_KEYS if key not in item]
    if missing:
        _fail(where, f"缺少字段 {missing}")

    case_id = item["id"]
    if not isinstance(case_id, str) or not case_id.strip():
        _fail(where, "id 不能为空")
    where = f"第 {index} 条 (id={case_id})"

    if item["category"] not in CATEGORIES:
        _fail(where, f"category `{item['category']}` 不在枚举 {CATEGORIES} 里")
    if not isinstance(item["question"], str) or not item["question"].strip():
        _fail(where, "question 不能为空")

    gold_tables = item["gold_tables"]
    if not isinstance(gold_tables, list) or not gold_tables:
        _fail(where, "gold_tables 必须是非空列表")
    if not all(isinstance(table, str) and table for table in gold_tables):
        _fail(where, "gold_tables 里必须是表名字符串")

    gold_columns = item["gold_columns"]
    if not isinstance(gold_columns, list) or not gold_columns:
        _fail(where, "gold_columns 必须是非空列表")
    for column in gold_columns:
        if not isinstance(column, str) or "." not in column:
            _fail(where, f"gold_columns 里的 `{column}` 不是「表名.列名」格式")

    gold_metric = item.get("gold_metric")
    if gold_metric is not None and (
        not isinstance(gold_metric, str) or not gold_metric.strip()
    ):
        _fail(where, "gold_metric 要么不写, 要么是非空字符串")

    if not isinstance(item["gold_sql"], str) or not item["gold_sql"].strip():
        _fail(where, "gold_sql 不能为空")

    eex = item.get("eex", False)
    if not isinstance(eex, bool):
        _fail(where, "eex 只能是 true / false")

    if not isinstance(item["notes"], str) or not item["notes"].strip():
        _fail(where, "notes (口径说明) 不能为空")

    return GoldenCase(
        id=case_id,
        category=item["category"],
        question=item["question"],
        gold_tables=tuple(gold_tables),
        gold_columns=tuple(gold_columns),
        gold_metric=gold_metric,
        gold_sql=item["gold_sql"].strip(),
        eex=eex,
        notes=item["notes"].strip(),
    )


def parse_cases(payload: object) -> tuple[GoldenCase, ...]:
    """把 YAML 读出来的结构变成题目列表; 任何格式问题当场报错."""
    if not isinstance(payload, list) or not payload:
        _fail("题库", "顶层必须是非空列表")

    cases = tuple(_as_case(item, index + 1) for index, item in enumerate(payload))

    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            _fail(f"题库 (id={case.id})", "id 重复")
        seen.add(case.id)
    return cases


def load_golden_set(path: Path | None = None) -> tuple[GoldenCase, ...]:
    path = path or DEFAULT_GOLDEN_SET_PATH
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_cases(payload)
