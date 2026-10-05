"""报告渲染: JSON 给程序 diff, Markdown 给人看 (与 CharApp/eval 的报告形状对齐).

JSON 是规范形状 —— 重跑后 `git diff` 就能看出哪道题退化了; Markdown 是讲述形状 ——
面试时打开的正是这一份: 头部一行"两个数字", 尾部一段"不做什么与为什么".
"""

import json
from pathlib import Path

REPORTS_DIR = Path(__file__).parent / "reports"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _pp(value: float | None) -> str:
    """极差用「百分点」(percentage point) 表示."""
    return "n/a" if value is None else f"{value * 100:.1f}pp"


def _num(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _spread_cell(spread_block: dict | None, digits: int = 3) -> str:
    """「均值 (±极差)」—— 逐题块用 `range`, 汇总块用 `avg_range`; 都没有就显 n/a.

    未知极差**不许**显示成 0.0: 那看起来像「稳如泰山」, 实际是「没跑够次数」.
    """
    if spread_block is None:
        return "n/a"
    rng = spread_block.get("range", spread_block.get("avg_range"))
    if rng is None:
        return _num(spread_block["mean"], digits)
    return f"{_num(spread_block['mean'], digits)} (±{_num(rng, digits)})"


def _block_pct(block: dict | None) -> str:
    """0/1 类型的指标块 (表命中 / 指标命中) 按百分比显示均值."""
    return "n/a" if block is None else f"{block['mean'] * 100:.0f}%"


def render_markdown(report: dict) -> str:
    meta = report["meta"]
    summary = report["summary"]
    l1 = summary["l1"]

    # 头部这些字段只作展示: 缺了就显 n/a, 不让渲染层在跑完几个小时之后才崩
    model = meta.get("model", "n/a")
    times = meta.get("times", "n/a")
    case_count = meta.get("case_count", "n/a")
    duration_min = meta.get("duration_s")
    duration_text = "n/a" if duration_min is None else f"{duration_min / 60:.1f} 分钟"

    lines: list[str] = []
    lines.append("# rag_text2sql 评估报告 — L1 Schema 召回 + L2 SQL 可执行率")
    lines.append("")
    lines.append(
        f"> 跑批时间: {meta.get('run_at', 'n/a')} | 模型: `{model}` | "
        f"每题 {times} 次 | {case_count} 题 | "
        f"用时 {duration_text}"
    )
    lines.append(
        "> 候选集数据源: 图跑到 `filter_table` / `filter_metric` **之后**的"
        "节点输出 (不是最终 SQL 文本 —— 这样召回错与生成错分得开)"
    )
    limits = meta.get("limits") or {}
    lines.append(
        f"> 护栏: 只读白名单 + LIMIT 补齐 {limits.get('default_limit', 'n/a')}"
        f" / 上限 {limits.get('cap_limit', 'n/a')} + 只读事务与语句预算"
        " —— C16 起这就是生产链路的执行咽喉, 跑批与图内同一份实现"
    )
    rerun = meta.get("rerun")
    if rerun:
        history = rerun.get("previous_failures") or []
        history_text = f" · 上一版失败: {'; '.join(history)}" if history else ""
        lines.append(
            f"> **复跑并回**: {rerun['at']} · 题目 {', '.join(rerun['cases'])}"
            f" ({rerun.get('note', '')}){history_text}"
        )
    lines.append("")

    lines.append("## 一, 两个数字 (验收口径)")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    recall_block = l1.get("column_recall") or {}
    lines.append(
        f"| **L1 列召回率** | **{_pct(recall_block.get('mean'))}**"
        f" (臂内平均极差 {_pp(recall_block.get('avg_range'))}) |"
    )
    ex_block = summary["l2"].get("ex_rate") or {}
    ex_range = ex_block.get("avg_range")
    ex_suffix = f" (臂内平均极差 {_pp(ex_range)})" if ex_range is not None else ""
    runs_with_sql = summary["l2"].get("runs_with_sql")
    ex_scope = (
        f" (运行失败计 0 —— 其中 {runs_with_sql} 次产出了 SQL)"
        if runs_with_sql is not None
        else ""
    )
    lines.append(
        f"| **L2 SQL 可执行率 (EX)** | **{_pct(ex_block['mean'])}**{ex_suffix} |"
        f"{ex_scope} |"
    )
    if runs_with_sql is not None:
        lines.append(
            f"| └ 只看产出 SQL 的 {runs_with_sql} 次 | "
            f"{_pct(summary['l2'].get('ex_rate_with_sql'))} "
            "(「没跑完」与「SQL 跑不通」是两类: 这一行把前者剔出去) |"
        )
    reliability = summary["reliability"]
    lines.append(
        f"| 运行失败率 (图执行抛错 / 超时) | {_pct(reliability['crash_rate'])}"
        f" ({reliability['failed_runs']}/{reliability['run_count']} 次) |"
    )
    eex = summary["eex"]
    lines.append(
        f"| EEX 结果完全一致 ({eex['cases']} 道手算题) | {_pct(eex['pass_rate'])} |"
    )
    rejections = summary["l2"]["guard_rejections"]
    lines.append(
        f"| 生成 SQL 撞上执行护栏被拒 | {rejections} 次"
        " (模型写出过非只读语句的次数 —— 0 次也是结论) |"
    )
    lines.append("")

    lines.append("## 二,L1 总览")
    lines.append("")
    lines.append("| 指标 | 均值 (跨题池化) | 臂内平均极差 | 说明 |")
    lines.append("|---|---|---|---|")

    def summary_row(label: str, key: str, note: str) -> str:
        # 块可能是 None: 题目子集里没有指标题时 metric_hit_rate 就是 None ——
        # 渲染层必须容得下 (C15 起潜伏, C16 选单题复跑时才撞出来)
        block = l1.get(key) or {}
        return (
            f"| {label} | {_pct(block.get('mean'))} | "
            f"{_pp(block.get('avg_range'))} | {note} |"
        )

    lines.append(
        summary_row("列召回率", "column_recall", "过滤后的候选集里, gold 列找回多少")
    )
    lines.append(
        summary_row(
            "列精确率", "column_precision", "候选集里有多少是 gold 用不到的噪声"
        )
    )
    lines.append(
        summary_row("表必命中率", "table_hit_rate", "gold 表集合**全部**出现在候选里")
    )
    lines.append(
        summary_row("指标命中率", "metric_hit_rate", "有指标的题 (无指标题不参与)")
    )
    lines.append(
        summary_row(
            "召回@merge (诊断)",
            "column_recall_at_merge",
            "过滤**之前**的召回 —— 与上一行对比即知「召回丢」还是「过滤误删」",
        )
    )
    lines.append("")

    lines.append("## 三, 按类别")
    lines.append("")
    lines.append("| 类别 | 题数 | 列召回率 | 列精确率 | 表必命中 | EX |")
    lines.append("|---|---|---|---|---|---|")
    for block in report["by_category"]:
        lines.append(
            f"| {block['category']} | {block['cases']} | "
            f"{_pct(block['column_recall'])} | {_pct(block['column_precision'])} | "
            f"{_pct(block['table_hit_rate'])} | {_pct(block['ex_rate'])} |"
        )
    lines.append("")

    lines.append("## 四, 逐题表")
    lines.append("")
    lines.append(
        "| 题号 | 类别 | 问句 | 列召回 | 列精确 | 表命中 | 指标命中 | EX | EEX |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for case in report["per_case"]:
        metric_block = case["metric_hit_rate"]
        metric_cell = "—" if metric_block is None else _block_pct(metric_block)
        eex_cell = "—" if case["eex_rate"] is None else _pct(case["eex_rate"])
        ex_cell = _pct(case["ex_rate"])
        if case.get("ex_range"):
            ex_cell += f" (±{case['ex_range'] * 100:.0f}%)"
        lines.append(
            f"| {case['id']} | {case['category']} | {case['question']} | "
            f"{_spread_cell(case['column_recall'], 2)} | "
            f"{_spread_cell(case['column_precision'], 2)} | "
            f"{_block_pct(case['table_hit_rate'])} | {metric_cell} | "
            f"{ex_cell} | {eex_cell} |"
        )
    lines.append("")

    lines.append("## 五, 失败样本清单 (badcase)")
    lines.append("")
    if not report["failures"]:
        lines.append("本次跑批没有失败样本 (EX 全部通过且无运行失败).")
    else:
        for item in report["failures"]:
            detail = item["error"] or "EX 失败"
            kind = (
                "运行失败, 未产出 SQL" if item.get("failed_stage") else "SQL 执行失败"
            )
            lines.append(
                f"- **{item['id']}** (run {item['run']}, {item['category']}, {kind}): "
                f"{item['question']} — {detail}"
            )
            if item.get("sql_final"):
                lines.append(f"  - 测试 SQL: `{item['sql_final']}`")
    lines.append("")

    lines.append("## 六, 解读 (从本次数据里读出来的)")
    lines.append("")
    for finding in report.get("findings", []):
        lines.append(f"- {finding}")
    lines.append("")

    lines.append("## 七, 不做什么与为什么")
    lines.append("")
    for note in report["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"> 题库: `{meta.get('golden_set_path', 'app/eval/golden_set.yaml')}` | "
        "本报告由 `python -m app.eval.runner` 生成"
    )
    lines.append("")
    return "\n".join(lines)


def write_reports(report: dict, out_dir: Path | None = None, stem: str = "baseline"):
    """落 JSON + Markdown 两份, 返回 (json_path, md_path)."""
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"

    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
