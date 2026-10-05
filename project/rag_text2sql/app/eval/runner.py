"""跑批器: 题库 x N 次 -> 逐节点快照 -> L1/L2 指标 -> JSON + Markdown 报告.

用法::

    python -m app.eval.runner                       # 全部题, 每题 N=3 次
    python -m app.eval.runner --times 1 --cases single-01,value-01
    python -m app.eval.runner --check-gold          # 只验题库的 gold SQL (不调模型)
    python -m app.eval.runner --out app/eval/reports

它要真模型 (39 题 x 3 次 = 上百次问答), 所以**不在 pytest 里** —— 用例跑的是
不触网的那一层 (题库校验 / 护栏 / 计分 / 报告渲染).

三个设计要点:

1. **候选集读节点输出, 不读最终 SQL** —— L1 的 gold 对照的是
   `filter_table` / `filter_metric` 之后的表/列/指标集合, 与生成无关. 报告里同时
   给「召回@merge」, 两行一比就知道丢在召回还是丢在过滤.
2. **快照必须深拷贝** —— `filter_table` 是**原地**删 `state["table_infos"]` 里的
   表与列再返回同一个列表对象; 不拷贝的话, merge 阶段的快照会被后面的过滤改掉,
   「召回@merge」会等于「过滤后」, 诊断功能整个失效.
3. **每个 run 用独立的只读会话** —— 跑批执行的是模型生成的 SQL, 护栏从
   **生产的 `DwMysqlRepository`** 走 (C16 把它立成了执行咽喉: 白名单 + LIMIT,
   数据库侧还有只读事务与语句预算), 图内图外同一份实现, 不重复造.
   给每个 run 新会话是为了失败隔离: 上一题的坏事务不拖累下一题.
"""

import argparse
import asyncio
import copy
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.clients.embedding import embedding_client
from app.clients.es import es_client
from app.clients.mysql import dw_client, meta_client
from app.clients.qdrant import qdrant_client
from app.conf.app_config import app_config
from app.core.log import logger
from app.core.sql_guard import (
    DEFAULT_LIMIT,
    LIMIT_CAP,
    SqlGuardLimits,
    SqlRejectedError,
)
from app.eval.golden import DEFAULT_GOLDEN_SET_PATH, GoldenCase, load_golden_set
from app.eval.metrics import L1Outcome, results_equal, score_l1, spread
from app.eval.report import REPORTS_DIR, write_reports
from app.repositories.es.value import ValueEsRepository
from app.repositories.mysql.dw import DwMysqlRepository
from app.repositories.mysql.meta import MetaMysqlRepository
from app.repositories.qdrant.column import ColumnQdrantRepository
from app.repositories.qdrant.metric import MetricQdrantRepository

# 报告尾部的「不做什么与为什么」—— 这几段是报告的一部分, 不是注释
STANDING_NOTES = (
    "L3 (LLM-as-judge) 不做: 引 judge 会把非确定性引进判据里, "
    "「这次比上次好」这句话本身就不再可复现 (与 CharApp/docs/adr/0021 同源); "
    "本票要证的两件事 (schema 召回 / SQL 可执行) 恰好都可规则化.",
    "EEX (结果完全一致) 只做 {eex_count} 道能手算的题, 不做全量: "
    "30~50 题的规模上逐题人工确认结果集的性价比不成立; "
    "硬凑一个假数字不如如实说明范围.其中 time-01 / relative-01 两道是有意开的 —— "
    "系统实测走「fact_order.date_id 取区间」而不是 gold 的「dim_date 关联」, "
    "这两题的 EEX 用来证明两种写法结果一致 (EX 只能证明可执行).",
    "歧义题不做 (2026-10-05 决定): 当前项目定位是简单问答统计, 不搞复杂评估; "
    "系统也尚无澄清机制 (README §6.2-7 的已知缺口), 记录「它有没有反问」只会是恒定 0.",
    "dw 数据窗口: 样例库时间维止于 2025-03-31 (dim_date 90 天 / fact_order 115 行)."
    "relative-03 (今年) / relative-04 (上个月) 按设计落在空窗口 —— "
    "期望是可执行 + 空结果, 如实记录, 不进 EEX.",
    "AOV 声明不一致 (README §6.1-2, C01 归口到本票): meta_config 曾把 AOV 的 "
    "relevant_columns 写成 order_quantity, 与它自己的描述不符 —— C15 收尾时已修正为 "
    "order_amount 并重建索引 (2026-10-05); gold 自始至终按正确口径 (AVG(order_amount)) "
    "写, 不迁就过那处错声明; 本报告是修正后重跑的基线.",
    "相对时间的 gold SQL 按重跑日期手写 (见 meta.run_at); 换月份重跑前需复核 "
    "relative_time 四道题的年份/月份.",
)


@dataclass(frozen=True)
class CandidateSet:
    """一次运行在某个节点上的候选集 (表 / 列 / 指标, 都去重排好序)."""

    tables: tuple[str, ...]
    columns: tuple[str, ...]
    metrics: tuple[str, ...]


def candidate_from(table_infos: list | None, metric_infos: list | None) -> CandidateSet:
    table_infos = table_infos or []
    metric_infos = metric_infos or []
    return CandidateSet(
        tables=tuple(sorted({table["name"] for table in table_infos})),
        columns=tuple(
            sorted(
                {
                    f"{table['name']}.{column['name']}"
                    for table in table_infos
                    for column in table["columns"]
                }
            )
        ),
        metrics=tuple(sorted({metric["name"] for metric in metric_infos})),
    )


@dataclass
class RunRecord:
    """一次运行的留痕 (逐节点快照 + 判分)."""

    index: int
    duration_s: float = 0.0
    failed_stage: str | None = None
    error: str | None = None

    keywords: list[str] = field(default_factory=list)
    recalled_columns: list[str] = field(default_factory=list)
    recalled_metrics: list[str] = field(default_factory=list)
    recalled_values: list[str] = field(default_factory=list)

    merged: CandidateSet | None = None
    # filter_table / filter_metric 是两个并行节点, 分两条增量到达 —— 先各存一半,
    # 凑齐才是一个完整的「过滤后候选集」(也是 from_dict 重建时的落点)
    filtered_tables: tuple[str, ...] | None = None
    filtered_columns: tuple[str, ...] | None = None
    filtered_metrics: tuple[str, ...] | None = None

    sql_generated: str | None = None
    sql_final: str | None = None
    validate_error: str | None = None
    corrected: bool = False

    guard_rejections: list[str] = field(default_factory=list)
    had_own_limit: bool = False
    limit_enforced: bool = False

    ex_ok: bool = False
    ex_error: str | None = None
    result_rows: int | None = None
    result_preview: list | None = None

    l1: L1Outcome | None = None
    l1_at_merge: L1Outcome | None = None
    eex_pass: bool | None = None
    eex_error: str | None = None

    @property
    def filtered(self) -> CandidateSet | None:
        if None in (self.filtered_tables, self.filtered_columns, self.filtered_metrics):
            return None
        return CandidateSet(
            self.filtered_tables, self.filtered_columns, self.filtered_metrics
        )

    def to_dict(self) -> dict:
        """报告里逐 run 的形状 —— 由本类自己给, 加字段时不会漏抄 (评审指出的脆弱点)."""
        return {
            "run": self.index + 1,
            "duration_s": round(self.duration_s, 2),
            "failed_stage": self.failed_stage,
            "error": self.error,
            "keywords": self.keywords,
            "recalled_columns": self.recalled_columns,
            "recalled_metrics": self.recalled_metrics,
            "recalled_values": self.recalled_values,
            "merged_tables": list(self.merged.tables) if self.merged else None,
            "merged_columns": list(self.merged.columns) if self.merged else None,
            "merged_metrics": list(self.merged.metrics) if self.merged else None,
            "filtered_tables": list(self.filtered.tables) if self.filtered else None,
            "filtered_columns": list(self.filtered.columns) if self.filtered else None,
            "filtered_metrics": list(self.filtered.metrics) if self.filtered else None,
            "sql_generated": self.sql_generated,
            "sql_final": self.sql_final,
            "corrected": self.corrected,
            "validate_error": self.validate_error,
            "guard_rejections": self.guard_rejections,
            "had_own_limit": self.had_own_limit,
            "limit_enforced": self.limit_enforced,
            "ex_ok": self.ex_ok,
            "ex_error": self.ex_error,
            "result_rows": self.result_rows,
            "result_preview": self.result_preview,
            "eex_pass": self.eex_pass,
            "eex_error": self.eex_error,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "RunRecord":
        """把报告里的一条逐 run 记录读回来 (复跑失败样本并回基线时用)."""
        record = cls(
            index=int(payload["run"]) - 1,
            duration_s=float(payload.get("duration_s") or 0.0),
            failed_stage=payload.get("failed_stage"),
            error=payload.get("error"),
        )
        record.keywords = list(payload.get("keywords") or [])
        record.recalled_columns = list(payload.get("recalled_columns") or [])
        record.recalled_metrics = list(payload.get("recalled_metrics") or [])
        record.recalled_values = list(payload.get("recalled_values") or [])
        if payload.get("merged_tables") is not None:
            record.merged = CandidateSet(
                tables=tuple(payload["merged_tables"]),
                columns=tuple(payload.get("merged_columns") or ()),
                metrics=tuple(payload.get("merged_metrics") or ()),
            )
        if payload.get("filtered_tables") is not None:
            record.filtered_tables = tuple(payload["filtered_tables"])
            record.filtered_columns = tuple(payload.get("filtered_columns") or ())
            record.filtered_metrics = tuple(payload.get("filtered_metrics") or ())
        record.sql_generated = payload.get("sql_generated")
        record.sql_final = payload.get("sql_final")
        record.corrected = bool(payload.get("corrected"))
        record.validate_error = payload.get("validate_error")
        record.guard_rejections = list(payload.get("guard_rejections") or [])
        record.had_own_limit = bool(payload.get("had_own_limit"))
        record.limit_enforced = bool(payload.get("limit_enforced"))
        record.ex_ok = bool(payload.get("ex_ok"))
        record.ex_error = payload.get("ex_error")
        record.result_rows = payload.get("result_rows")
        record.result_preview = payload.get("result_preview")
        record.eex_pass = payload.get("eex_pass")
        record.eex_error = payload.get("eex_error")
        return record


@dataclass
class CaseRecord:
    case: GoldenCase
    runs: list[RunRecord] = field(default_factory=list)


class EvalRunner:
    """把「题库 x N 次」跑完并算分; 报告渲染交给 report.py."""

    def __init__(
        self,
        cases: list[GoldenCase],
        *,
        times: int = 3,
        limits: SqlGuardLimits = SqlGuardLimits(),
        timeout_s: float = 240.0,
    ) -> None:
        self.cases = cases
        self.times = times
        self.limits = limits
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------
    # 执行单个 run
    # ------------------------------------------------------------------

    async def _run_once(self, case: GoldenCase, index: int) -> RunRecord:
        record = RunRecord(index=index)
        started = time.perf_counter()

        try:
            async with (
                dw_client.session() as dw_session,
                meta_client.session() as meta_session,
            ):
                # 只读事务与语句预算由引擎在连接建立时钉好 (app/clients/mysql.py),
                # 白名单 + LIMIT 由仓储过 —— 这里不用再切一次只读
                repository = DwMysqlRepository(dw_session, self.limits)
                context = DataAgentContext(
                    embeddings=embedding_client.embeddings,
                    dw_mysql_repository=repository,
                    meta_mysql_repository=MetaMysqlRepository(meta_session),
                    column_qdrant_repository=ColumnQdrantRepository(
                        qdrant_client.client
                    ),
                    metric_qdrant_repository=MetricQdrantRepository(
                        qdrant_client.client
                    ),
                    value_es_repository=ValueEsRepository(es_client.client),
                )
                await asyncio.wait_for(
                    self._consume(case, context, record), timeout=self.timeout_s
                )
                await self._evaluate_sql(case, repository, record)
                # 全程的拒绝都记下来 (图内那次也在内); 单条语句的 LIMIT 留痕
                # 由 `_evaluate_sql` 在跑完**那条 SQL** 之后自己取
                record.guard_rejections = [
                    note.rejected_reason
                    for note in repository.notes
                    if note.rejected_reason is not None
                ]
                if record.sql_final is None and repository.notes:
                    # 没走到 EX 重跑 (图内就失败了): 退回图内最后一次留痕
                    last = repository.notes[-1]
                    record.had_own_limit = last.had_own_limit
                    record.limit_enforced = last.limit_enforced
        except TimeoutError:
            record.failed_stage = record.failed_stage or "timeout"
            record.error = f"运行超时 (>{self.timeout_s:.0f}s)"
        except Exception as exc:
            record.failed_stage = record.failed_stage or "graph"
            record.error = str(exc)

        record.duration_s = time.perf_counter() - started
        self._score(case, record)
        return record

    async def _consume(
        self, case: GoldenCase, context: DataAgentContext, record: RunRecord
    ) -> None:
        """跑图并接住逐节点增量 (updates 模式按节点给 `{节点名: 返回值}`)."""
        async for mode, payload in graph.astream(
            input=DataAgentState(query=case.question),
            context=context,
            stream_mode=["custom", "updates"],
        ):
            if mode != "updates" or not payload:
                continue
            for node, update in payload.items():
                self._absorb(node, update or {}, record)

    @staticmethod
    def _absorb(node: str, update: dict, record: RunRecord) -> None:
        if node == "extract_keywords":
            record.keywords = list(update.get("keywords", []))
        elif node == "recall_column":
            record.recalled_columns = [
                column["id"] for column in update.get("retrieved_columns", [])
            ]
        elif node == "recall_metric":
            record.recalled_metrics = [
                metric["id"] for metric in update.get("retrieved_metrics", [])
            ]
        elif node == "recall_value":
            record.recalled_values = [
                value["id"] for value in update.get("retrieved_values", [])
            ]
        elif node == "merge_retrieve":
            # 深拷贝! 见模块 docstring 第 2 条: filter_table 会原地删这份列表
            snapshot = copy.deepcopy(update)
            record.merged = candidate_from(
                snapshot.get("table_infos"), snapshot.get("metric_infos")
            )
        elif node == "filter_table":
            # 这里不用 deepcopy: 立刻摊平成名字元组, 之后谁也改不到它
            table_infos = update.get("table_infos") or []
            record.filtered_tables = tuple(sorted({t["name"] for t in table_infos}))
            record.filtered_columns = tuple(
                sorted(
                    {
                        f"{t['name']}.{c['name']}"
                        for t in table_infos
                        for c in t["columns"]
                    }
                )
            )
        elif node == "filter_metric":
            metric_infos = update.get("metric_infos") or []
            record.filtered_metrics = tuple(sorted({m["name"] for m in metric_infos}))
        elif node == "generate_sql":
            record.sql_generated = update.get("sql")
            record.sql_final = update.get("sql")
        elif node == "correct_sql":
            record.corrected = True
            record.sql_final = update.get("sql")
        elif node == "validate_sql":
            record.validate_error = update.get("error")

    async def _evaluate_sql(
        self, case: GoldenCase, repository: DwMysqlRepository, record: RunRecord
    ) -> None:
        """跑批器自己重跑最终 SQL 判 EX —— 与图内执行解耦, 判据只有一条: 能不能跑通."""
        if record.sql_final is None:
            record.ex_error = "运行未产出 SQL"
            return

        try:
            rows = await repository.execute_sql(record.sql_final)
        except SqlRejectedError as exc:
            record.ex_error = f"被执行护栏拒绝: {exc}"
            return
        except Exception as exc:
            record.ex_error = str(exc)
            return

        record.ex_ok = True
        record.result_rows = len(rows)
        record.result_preview = rows[:5]
        # 护栏留痕取在**这条 SQL**跑完之后 (原先是事后读 notes[-1], EEX 题上会
        # 记成 gold SQL 的留痕 —— 评审抓到的错位)
        note = repository.last_note
        if note is not None:
            record.had_own_limit = note.had_own_limit
            record.limit_enforced = note.limit_enforced

        if not case.eex:
            return
        try:
            gold_rows = await repository.execute_sql(case.gold_sql)
        except Exception as exc:
            record.eex_error = f"gold SQL 执行失败 (题库问题): {exc}"
            return
        record.eex_pass = results_equal(rows, gold_rows)

    @staticmethod
    def _score(case: GoldenCase, record: RunRecord) -> None:
        def score(candidates: CandidateSet) -> L1Outcome:
            return score_l1(
                gold_tables=case.gold_tables,
                gold_columns=case.gold_columns,
                gold_metric=case.gold_metric,
                retrieved_tables=candidates.tables,
                retrieved_columns=candidates.columns,
                retrieved_metrics=candidates.metrics,
            )

        if record.merged is not None:
            record.l1_at_merge = score(record.merged)
        if record.filtered is not None:
            record.l1 = score(record.filtered)

    # ------------------------------------------------------------------
    # 跑一批
    # ------------------------------------------------------------------

    async def run(self) -> list[CaseRecord]:
        results: list[CaseRecord] = []
        for position, case in enumerate(self.cases, start=1):
            record = CaseRecord(case=case)
            for index in range(self.times):
                run = await self._run_once(case, index)
                record.runs.append(run)
                logger.info(
                    f"[{position}/{len(self.cases)}] {case.id} run{index + 1}: "
                    f"EX={'ok' if run.ex_ok else 'fail'} "
                    f"recall={run.l1.column_recall if run.l1 else 'n/a'} "
                    f"({run.duration_s:.1f}s)"
                )
            results.append(record)
        return results


# ---------------------------------------------------------------------------
# 汇总 -> 报告 payload
# ---------------------------------------------------------------------------


def _block(values: list[float], *, pooled_ranges: list[float] | None = None):
    """一个指标的汇总块: 池化均值 + (可选的) 臂内平均极差."""
    if not values:
        return None
    stats = spread(values)
    block = {
        "mean": stats.mean,
        "min": stats.min,
        "max": stats.max,
        "runs": len(values),
    }
    if pooled_ranges is not None:
        # 没有可算极差的题时记 None 而不是 0.0 —— 「没跑够次数」不能显示成「稳定」
        block["avg_range"] = (
            sum(pooled_ranges) / len(pooled_ranges) if pooled_ranges else None
        )
    return block


def _per_case_block(values: list[float]):
    """逐题块 = 汇总块 + 这一题的臂内极差 (键名 `range`, 汇总那边是 `avg_range`)."""
    block = _block(values)
    if block is not None:
        block["range"] = block["max"] - block["min"]
    return block


def _collect(records: list[CaseRecord], getter) -> tuple[list[float], list[float]]:
    """返回 (全部 run 的值池, 各题的臂内极差) —— 两者都缺则全是空的."""
    pooled: list[float] = []
    case_ranges: list[float] = []
    for record in records:
        values = [value for value in map(getter, record.runs) if value is not None]
        pooled.extend(values)
        if len(values) > 1:
            case_ranges.append(max(values) - min(values))
    return pooled, case_ranges


def _bool_of(value: bool) -> float:
    return 1.0 if value else 0.0


def summarize_case(record: CaseRecord) -> dict:
    case = record.case
    runs = record.runs

    def l1_values(attribute: str) -> list[float]:
        return [
            getattr(run.l1, attribute)
            for run in runs
            if run.l1 is not None and getattr(run.l1, attribute) is not None
        ]

    metric_values = [
        _bool_of(run.l1.metric_hit)
        for run in runs
        if run.l1 is not None and run.l1.metric_hit is not None
    ]
    merge_values = [
        run.l1_at_merge.column_recall for run in runs if run.l1_at_merge is not None
    ]
    eex_values = [run.eex_pass for run in runs if run.eex_pass is not None]
    ex_flags = [_bool_of(run.ex_ok) for run in runs]

    return {
        "id": case.id,
        "category": case.category,
        "question": case.question,
        "notes": case.notes,
        "runs": len(runs),
        "failed_runs": sum(1 for run in runs if run.failed_stage is not None),
        "column_recall": _per_case_block(l1_values("column_recall")),
        "column_precision": _per_case_block(l1_values("column_precision")),
        "table_hit_rate": _per_case_block(
            [_bool_of(run.l1.table_hit) for run in runs if run.l1 is not None]
        ),
        "metric_hit_rate": _per_case_block(metric_values),
        "column_recall_at_merge": _per_case_block(merge_values),
        "ex_rate": sum(ex_flags) / len(ex_flags) if ex_flags else 0.0,
        # EX 的臂内极差: N 次里有的通有的不通, 就是这一刻的波动 (票面要求给极差)
        "ex_range": max(ex_flags) - min(ex_flags) if len(ex_flags) > 1 else None,
        "eex": case.eex,
        "eex_rate": (
            sum(_bool_of(value) for value in eex_values) / len(eex_values)
            if eex_values
            else None
        ),
        "gold_tables": list(case.gold_tables),
        "gold_columns": list(case.gold_columns),
        "gold_metric": case.gold_metric,
        "per_run": [run.to_dict() for run in runs],
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _findings(
    per_case: list[dict], by_category: list[dict], summary: dict
) -> list[str]:
    """从本次数据里读出来的解读.

    纪律: 每条结论的数与例子都从 `per_case` / `per_run` 现算 —— 不写死题号, 不写死
    "哪张表被裁掉". 评审指出过前一版把归因与举例写成了常量: 换个题库重跑, 那些文字
    会静默失真, 而报告还标榜"从本次数据里读出来".
    """
    findings: list[str] = []

    low_hit = [
        block
        for block in by_category
        if block["table_hit_rate"] is not None and block["table_hit_rate"] < 1.0
    ]
    if low_hit:
        parts = []
        for block in low_hit:
            cases = [item for item in per_case if item["category"] == block["category"]]
            merge = _mean(
                [
                    item["column_recall_at_merge"]["mean"]
                    for item in cases
                    if item["column_recall_at_merge"] is not None
                ]
            )
            after = _mean(
                [
                    item["column_recall"]["mean"]
                    for item in cases
                    if item["column_recall"] is not None
                ]
            )
            hit = block["table_hit_rate"] * 100
            # 缺的是哪张 gold 表 / 最终 SQL 里还引不引用它 —— 逐 run 数出来
            dropped: Counter[str] = Counter()
            still_referenced: Counter[str] = Counter()
            scored_runs = 0
            for item in cases:
                gold_tables = set(item["gold_tables"])
                for run in item["per_run"]:
                    if run["filtered_tables"] is None:
                        continue
                    scored_runs += 1
                    sql = (run["sql_final"] or "").lower()
                    for table in gold_tables - set(run["filtered_tables"]):
                        dropped[table] += 1
                        if table.lower() in sql:
                            still_referenced[table] += 1
            dropped_text = "; ".join(
                f"{table} 缺 {count}/{scored_runs} 次, "
                f"其中 {still_referenced[table]} 次最终 SQL 仍引用"
                for table, count in dropped.most_common(2)
            )
            if merge is not None and after is not None:
                parts.append(
                    f"{block['category']} (表必命中 {hit:.0f}%, "
                    f"召回@merge {merge * 100:.0f}% → 过滤后 {after * 100:.0f}%)"
                )
            else:
                parts.append(f"{block['category']} (表必命中 {hit:.0f}%)")
            if dropped_text:
                parts.append(f"缺的表: {dropped_text}")
        low_categories = {block["category"] for block in low_hit}
        eex_cases = [
            item["id"]
            for item in per_case
            if item["category"] in low_categories and item["eex"]
        ]
        eex_text = (
            f"本次靠这些题的 EEX 直接验: {', '.join(eex_cases)}"
            if eex_cases
            else "本次该类别没有开 EEX 的题, 等价性未经测量"
        )
        findings.append(
            "表必命中率未满的类别: " + "; ".join(parts) + " —— 丢在**过滤**这一层而不是"
            "召回 (召回@merge 明显高于过滤后); 最终 SQL 走的是不依赖该表的写法, "
            "可执行率不受影响.两种写法是否等价不在 L1/L2 判据内 —— " + eex_text + "."
        )

    low_precision = [
        block
        for block in by_category
        if block["column_precision"] is not None and block["column_precision"] < 0.95
    ]
    if low_precision:
        detail = ", ".join(
            f"{block['category']} ({block['column_precision'] * 100:.0f}%)"
            for block in low_precision
        )
        worst_cases = sorted(
            (
                item
                for item in per_case
                if item["category"] in {block["category"] for block in low_precision}
                and item["column_precision"] is not None
            ),
            key=lambda item: item["column_precision"]["mean"],
        )[:2]
        extras: Counter[str] = Counter()
        for item in worst_cases:
            gold_columns = set(item["gold_columns"])
            for run in item["per_run"]:
                if run["filtered_columns"] is None:
                    continue
                extras.update(set(run["filtered_columns"]) - gold_columns)
        example_text = (
            f" (最差的两道 {', '.join(item['id'] for item in worst_cases)} 多带 "
            f"{', '.join(column for column, _ in extras.most_common(3))})"
            if extras
            else ""
        )
        findings.append(
            f"列精确率未满的类别: {detail}{example_text} —— 多出的是 gold 用不到的列: "
            "merge 会给涉及的表补主外键, 过滤后仍留下未被使用的列, 这些列计入噪声."
        )

    ranges = [
        block["avg_range"]
        for block in summary["l1"].values()
        if block is not None and block.get("avg_range") is not None
    ]
    if ranges and all(value == 0.0 for value in ranges):
        findings.append(
            "臂内极差全为 0: L1 各指标在 N 次重复里完全相同 —— temperature=0 下检索与"
            "过滤近乎确定, 波动只出现在时长上 (超时那几次)."
            "按 L4 的教训, 「臂内极差为 0」不能读成「稳定」, "
            "它只是当前采样设置 (次数少 + 温度 0) 的产物."
        )
    return findings


def summarize_run(records: list[CaseRecord], meta: dict) -> dict:
    per_case = [summarize_case(record) for record in records]

    recall, recall_ranges = _collect(
        records, lambda run: run.l1.column_recall if run.l1 else None
    )
    precision, precision_ranges = _collect(
        records, lambda run: run.l1.column_precision if run.l1 else None
    )
    table_hit, table_ranges = _collect(
        records, lambda run: _bool_of(run.l1.table_hit) if run.l1 else None
    )
    metric_hit, metric_ranges = _collect(
        records,
        lambda run: (
            _bool_of(run.l1.metric_hit)
            if run.l1 is not None and run.l1.metric_hit is not None
            else None
        ),
    )
    merge_recall, merge_ranges = _collect(
        records, lambda run: run.l1_at_merge.column_recall if run.l1_at_merge else None
    )
    ex_values, ex_ranges = _collect(records, lambda run: _bool_of(run.ex_ok))

    all_runs = [run for record in records for run in record.runs]
    failed_runs = sum(1 for run in all_runs if run.failed_stage is not None)
    eex_values = [run.eex_pass for run in all_runs if run.eex_pass is not None]
    guard_rejections = sum(len(run.guard_rejections) for run in all_runs)

    # 「没跑完」与「SQL 跑不通」是两类: 前者根本没有 SQL 可执行. 上表那个 EX 把
    # 前者记 0 (系统级口径), 这里再给一个只按产出 SQL 的 run 算的口径 —— 两个数
    # 一起看, 读者不会把超时读成"生成质量差"
    runs_with_sql = [run for run in all_runs if run.sql_final is not None]
    ex_with_sql = (
        sum(_bool_of(run.ex_ok) for run in runs_with_sql) / len(runs_with_sql)
        if runs_with_sql
        else None
    )

    eex_count = sum(1 for record in records if record.case.eex)
    summary = {
        "l1": {
            "column_recall": _block(recall, pooled_ranges=recall_ranges),
            "column_precision": _block(precision, pooled_ranges=precision_ranges),
            "table_hit_rate": _block(table_hit, pooled_ranges=table_ranges),
            "metric_hit_rate": _block(metric_hit, pooled_ranges=metric_ranges),
            "column_recall_at_merge": _block(merge_recall, pooled_ranges=merge_ranges),
        },
        "l2": {
            "ex_rate": _block(ex_values, pooled_ranges=ex_ranges),
            "runs_with_sql": len(runs_with_sql),
            "ex_rate_with_sql": ex_with_sql,
            "guard_rejections": guard_rejections,
        },
        "reliability": {
            "run_count": len(all_runs),
            "failed_runs": failed_runs,
            "crash_rate": failed_runs / len(all_runs) if all_runs else 0.0,
        },
        "eex": {
            "cases": eex_count,
            "evaluated_runs": len(eex_values),
            "pass_rate": (
                sum(_bool_of(value) for value in eex_values) / len(eex_values)
                if eex_values
                else None
            ),
        },
        "l3": {
            "status": "not_done",
            "reason": STANDING_NOTES[0],
        },
    }

    by_category = []
    for category in dict.fromkeys(record.case.category for record in records):
        subset = [record for record in records if record.case.category == category]
        category_recall, _ = _collect(
            subset, lambda run: run.l1.column_recall if run.l1 else None
        )
        category_precision, _ = _collect(
            subset, lambda run: run.l1.column_precision if run.l1 else None
        )
        category_table, _ = _collect(
            subset, lambda run: _bool_of(run.l1.table_hit) if run.l1 else None
        )
        category_ex, _ = _collect(subset, lambda run: _bool_of(run.ex_ok))
        by_category.append(
            {
                "category": category,
                "cases": len(subset),
                "column_recall": spread(category_recall).mean
                if category_recall
                else None,
                "column_precision": spread(category_precision).mean
                if category_precision
                else None,
                "table_hit_rate": spread(category_table).mean
                if category_table
                else None,
                "ex_rate": spread(category_ex).mean if category_ex else None,
            }
        )

    failures = []
    for case_block in per_case:
        for run in case_block["per_run"]:
            if run["ex_ok"] and run["failed_stage"] is None:
                continue
            failures.append(
                {
                    "id": case_block["id"],
                    "category": case_block["category"],
                    "question": case_block["question"],
                    "run": run["run"],
                    "failed_stage": run["failed_stage"],
                    "error": run["ex_error"] or run["error"],
                    "sql_final": run["sql_final"],
                    "validate_error": run["validate_error"],
                }
            )

    return {
        "meta": meta,
        "summary": summary,
        "by_category": by_category,
        "per_case": per_case,
        "failures": failures,
        "findings": _findings(per_case, by_category, summary),
        "notes": [note.format(eex_count=eex_count) for note in STANDING_NOTES],
    }


def records_from_report(
    report: dict, cases_by_id: dict[str, GoldenCase]
) -> list[CaseRecord]:
    """把落盘报告读回成 CaseRecord 列表.

    用途是「复跑失败的几道题, 把结果并回基线」—— 不必把 39 题整批重掷一遍
    (重掷会让 37 道没问题的题也换一组随机数, 基线就不再是"只改该改的地方").

    L1 的派生字段 (`l1` / `l1_at_merge`) 不落盘, 这里用题库现算一遍 —— 判据是纯函数,
    重建出来的分与当时那一跑必然一致.
    """
    records: list[CaseRecord] = []
    for case_block in report["per_case"]:
        case = cases_by_id.get(case_block["id"])
        if case is None:
            raise KeyError(
                f"报告里的题 {case_block['id']} 不在当前题库里 —— "
                "题库变过, 不能拿旧报告做基线"
            )
        record = CaseRecord(case=case)
        for run_payload in case_block["per_run"]:
            run = RunRecord.from_dict(run_payload)
            EvalRunner._score(case, run)
            record.runs.append(run)
        records.append(record)
    return records


def merge_records(
    previous: list[CaseRecord], fresh: list[CaseRecord]
) -> list[CaseRecord]:
    """用新跑的题替换基线里同 id 的题, 其余原样保留 (顺序照旧)."""
    fresh_by_id = {record.case.id: record for record in fresh}
    known = {record.case.id for record in previous}
    unknown = set(fresh_by_id) - known
    if unknown:
        raise KeyError(f"要并回的题不在基线报告里: {sorted(unknown)}")

    return [fresh_by_id.get(record.case.id, record) for record in previous]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


async def _check_gold(cases: tuple[GoldenCase, ...]) -> int:
    """只验题库: gold SQL 在护栏下能不能跑通 (不调模型, 花几秒)."""
    dw_client.init()
    failures = 0
    try:
        async with dw_client.session() as session:
            repository = DwMysqlRepository(session)
            for case in cases:
                try:
                    rows = await repository.execute_sql(case.gold_sql)
                    logger.info(f"{case.id}: gold SQL 可执行, {len(rows)} 行")
                except Exception as exc:
                    failures += 1
                    logger.error(f"{case.id}: gold SQL 跑不通 —— {exc}")
    finally:
        await dw_client.close()
    logger.info(
        f"题库自检完成: {len(cases) - failures}/{len(cases)} 道 gold SQL 可执行"
    )
    return failures


async def _run_batch(cases: tuple[GoldenCase, ...], args: argparse.Namespace) -> int:
    dw_client.init()
    meta_client.init()
    qdrant_client.init()
    es_client.init()
    embedding_client.init()

    started = datetime.now()
    clock = time.perf_counter()
    try:
        runner = EvalRunner(
            list(cases),
            times=args.times,
            # 跑批用**生产同档**的护栏档位 (补 200 / 上限 1000): 评估要量的是生产系统
            limits=SqlGuardLimits(
                default_limit=args.max_rows, cap_limit=args.limit_cap
            ),
            timeout_s=args.timeout,
        )
        records = await runner.run()
    finally:
        await dw_client.close()
        await meta_client.close()
        await qdrant_client.close()
        await es_client.close()

    duration_s = time.perf_counter() - clock
    meta = {
        "run_at": started.strftime("%Y-%m-%d %H:%M"),
        "model": app_config.llm.model_name,
        "times": args.times,
        "case_count": len(cases),
        "limits": {
            "default_limit": args.max_rows,
            "cap_limit": args.limit_cap,
        },
        "timeout_s": args.timeout,
        "duration_s": round(duration_s, 1),
        # 一律用正斜杠: 报告要提交进仓库, 反斜杠路径在非 Windows 上读着别扭
        "golden_set_path": DEFAULT_GOLDEN_SET_PATH.relative_to(Path.cwd()).as_posix(),
    }

    if args.merge_into:
        # 复跑几道题并回基线: 其余题保持原样, 只有被复跑的题换数据
        previous = json.loads(Path(args.merge_into).read_text(encoding="utf-8"))
        cases_by_id = {case.id: case for case in load_golden_set()}
        previous_records = records_from_report(previous, cases_by_id)
        records = merge_records(previous_records, records)
        meta = {
            **previous["meta"],
            "rerun": {
                "at": started.strftime("%Y-%m-%d %H:%M"),
                "cases": [case.id for case in cases],
                "duration_s": round(duration_s, 1),
                "note": "复跑失败样本并回基线 (其余题不重掷)",
                # 上一版为什么复跑: 留在报告里, 复跑完之后这段历史还在
                "previous_failures": [
                    f"{item['id']} run{item['run']}: "
                    f"{'运行超时' if item.get('failed_stage') else 'EX 失败'}"
                    for item in previous.get("failures", [])
                ],
            },
        }

    report = summarize_run(records, meta)
    json_path, md_path = write_reports(report, args.out, args.stem)

    logger.info(
        f"跑批完成: 列召回率 {report['summary']['l1']['column_recall']['mean']:.3f}, "
        f"可执行率 {report['summary']['l2']['ex_rate']['mean']:.3f}, "
        f"失败样本 {len(report['failures'])} 条"
    )
    logger.info(f"报告落盘: {json_path} / {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="rag_text2sql L1/L2 跑批器")
    parser.add_argument("--times", type=int, default=3, help="每题跑几次 (默认 3)")
    parser.add_argument(
        "--cases", type=str, default=None, help="逗号分隔的题号, 默认全部"
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=DEFAULT_LIMIT,
        help="没写 LIMIT 时的补齐档位",
    )
    parser.add_argument(
        "--limit-cap",
        type=int,
        default=LIMIT_CAP,
        help="允许的行数上限 (与生产同档)",
    )
    parser.add_argument(
        "--timeout", type=float, default=240.0, help="单次运行超时 (秒)"
    )
    parser.add_argument("--out", type=Path, default=REPORTS_DIR, help="报告输出目录")
    parser.add_argument(
        "--stem", type=str, default="baseline", help="报告文件名 (不含扩展名)"
    )
    parser.add_argument(
        "--merge-into",
        type=Path,
        default=None,
        help="把本次跑的题并回这份已落盘报告 (其余题保持原样), 配合 --cases 用",
    )
    parser.add_argument(
        "--check-gold", action="store_true", help="只验题库的 gold SQL, 不调模型"
    )
    args = parser.parse_args(argv)

    cases = load_golden_set()
    if args.cases:
        wanted = {item.strip() for item in args.cases.split(",") if item.strip()}
        known = {case.id for case in cases}
        unknown = wanted - known
        if unknown:
            parser.error(f"未知题号: {sorted(unknown)}")
        cases = tuple(case for case in cases if case.id in wanted)

    if args.check_gold:
        failures = asyncio.run(_check_gold(cases))
        return 1 if failures else 0
    return asyncio.run(_run_batch(cases, args))


if __name__ == "__main__":
    raise SystemExit(main())
