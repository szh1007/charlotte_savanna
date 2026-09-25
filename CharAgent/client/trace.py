"""只读入口: 给一个 run_id, 把这次运行**做过什么、花了多少**打出来 (ticket 28).

用法::

    python -m CharAgent.client.trace <run_id>            # 账 + 工具调用清单
    python -m CharAgent.client.trace <run_id> --view     # 再加每帧的视图 (第二档)

一句话理解: 这是**回看一次运行的那扇窗** —— 不改任何东西, 只把库里那份事实读出来
摆成人能看的样子. 与 `python -m CharAgent.client` 分开的理由: 那个是交互式 REPL
(敲一句问一句, 进程一直活着), 这个是「给个编号, 打印, 退出」—— 两种生命周期搅在
一起, 谁的参数都说不清 (ticket 28 决定三).

三件事在这里各有一处落地:

1. **工具调用清单** —— 每一次调用的工具名 / 参数 (原样 JSON) / 状态 / 耗时 / 结果
   (`charagent_tool_calls`, 由记录层在执行前后各写一次);
2. **三档用量与金额** —— 全部从 `charagent_runs` 读: 用量是那五列, 金额与算式是
   `total_cost` / `total_cost_detail` 两列. **本入口不自己算钱** —— 钱在运行收尾
   那一刻就按当时的价目表算好写死了 (`db/cost.py` 是那套算法). 理由: 供应商会
   调价, 而账单是按它当时那一版开的; 查询侧现算会让历史运行的金额跟着今天的价变,
   与账单永远对不上;
3. **视图 (第二档, `--view`)** —— 帧里那份「那一轮真的发出去的是什么」+ 估算漂移
   与缓存命中率 (`charagent_checkpoints.metadata`).

**连接从哪来**: 本入口自己开一个 `PgDatabase` (读 `CHARAGENT_DB_DSN`, 或回退根 .env
的 `PGSQL_*`), 与 `client/app.py` 的 `_recorder_for` 没有关系 —— 那个只在 Postgres
快照后端下才配记录员, 而**回看历史运行**是另一件事: 它不该要求「这次也用 Postgres
后端」. 只读的三个查询各开一次事务, 不跨事务拼一致性 (这是给人看的视图, 不是对账).

**视图那一档读的是库里那份原样的 `metadata`**, 不绕 `checkpoint` 的序列化层: 那一层
的职责是「把帧读回来接着跑」, 而这里要的是「当时落进去的是什么」—— 老帧的视图
形状可能与当前版本不同, 如实显示比按新版升级过再显示更贴近事实.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from CharAgent.checkpoint.utils.history import display_width
from CharAgent.client.app import load_root_env, use_utf8_stdio
from CharAgent.db import Database, PgDatabase
from CharAgent.db.cost import CostGap, RunCost
from CharAgent.db.entities import Run, Thread, ToolCall
from CharAgent.db.errors import DbError
from CharAgent.db.repositories.runs import RunsRepository
from CharAgent.db.repositories.threads import ThreadsRepository
from CharAgent.db.repositories.tool_calls import ToolCallsRepository
from CharAgent.db.schema import checkpoints

# 表格的缩进与列分隔 (与 checkpoint/utils/history.py 那张历史表同一套排法)
_TABLE_INDENT = "  "
_SEPARATOR = "  "
_RESULT_INDENT = "      "

# 工具调用表的列 (顺序即列顺序) 与右对齐的列 (序号: 一位数与两位数要对齐)
_COLUMNS = ("#", "工具名", "状态", "耗时", "参数")
_RIGHT_ALIGNED = frozenset({"#"})

# 参数与结果的截断宽度 (字符数): 参数是模型填的原始 JSON, 结果可能是整段文本 ——
# 一行几百字会把表撑得没法看, 但截了必须报出总长 (见 _short)
_ARG_WIDTH = 60
_RESULT_WIDTH = 200


@dataclass(frozen=True, slots=True)
class FrameView:
    """一帧的**视图观察值** (帧 metadata 里那一份, 原样).

    attributes:
        turn_number: 这一帧存下时跑完几轮.
        created_at: 存下的时刻.
        view: 那一轮真的发出去那份的载荷 (`agent/compaction.py` 的 `view_payload`
            产物 + 事后补的诊断值); None = 这一轮没有模型调用 (挂起补做那一轮),
            或老帧里根本没这一项.
    """

    turn_number: int
    created_at: datetime
    view: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class TraceData:
    """一次运行的轨迹: 账 (runs) + 归属 (threads) + 干了什么 (tool_calls) + 帧.

    attributes:
        run: 那一行运行 (状态 / 模型 / 账目与时刻都在它上面).
        thread: 它属于哪个会话 (租户与属主在会话行上 —— runs 表本身没有这两列);
            None = 读不到那一行 (只读入口据此打 `-`, 不因此整段不显示).
        calls: 这次运行的工具调用 (按发起时刻正序).
        frames: 这次运行落的帧的视图 (`--view` 才读; 不读时是空元组).
    """

    run: Run
    thread: Thread | None
    calls: tuple[ToolCall, ...] = ()
    frames: tuple[FrameView, ...] = ()


# ---------------------------------------------------------------------------
# 读 (异步, 只读)
# ---------------------------------------------------------------------------


async def load_trace(
    database: Database, run_id: str, *, with_frames: bool = False
) -> TraceData | None:
    """把一次运行的轨迹读出来 (没有这个编号 -> None).

    Args:
        database: 数据库入口 (`PgDatabase`; 只要能被仓储用即可).
        run_id: 要看的运行编号.
        with_frames: 是否连帧的视图一起读 (第二档 `--view` 用).

    Returns:
        TraceData | None: 轨迹; None = 库里没有这个编号.

    Raises:
        DataStoreError: 库连不上 / 读失败.
    """
    run = await RunsRepository(database).get(run_id)
    if run is None:
        return None
    thread = await ThreadsRepository(database).get(run.thread_id)
    calls = await ToolCallsRepository(database).list_for_run(run_id)
    frames = await _load_frames(database, run_id) if with_frames else ()
    return TraceData(run=run, thread=thread, calls=tuple(calls), frames=frames)


async def _load_frames(database: Database, run_id: str) -> tuple[FrameView, ...]:
    """读这次运行落的帧 (只取渲染要的三列, 不反序列化整帧).

    帧的 `run_id` 是记录层那一行的外键 (ticket 22), 所以一条 WHERE 就能拿到全部
    —— 不必顺 `parent_id` 往回走 (那是「从头排到尾是一条链」的查法, 对不上「这一次
    运行落了哪些帧」这个问题).
    """
    statement = (
        select(
            checkpoints.c.turn_number,
            checkpoints.c.created_at,
            checkpoints.c.metadata,
        )
        .where(checkpoints.c.run_id == run_id)
        .order_by(checkpoints.c.turn_number.asc(), checkpoints.c.created_at.asc())
    )
    async with database.connect() as session:
        rows = session.execute(statement).all()
    return tuple(
        FrameView(
            turn_number=row.turn_number,
            created_at=row.created_at,
            view=_view_of(row.metadata),
        )
        for row in rows
    )


def _view_of(metadata: object) -> Mapping[str, Any] | None:
    """帧的 metadata -> 里面那份 view (取不到就是 None: 老帧 / 没模型调用那轮)."""
    if not isinstance(metadata, Mapping):
        return None
    view = metadata.get("view")
    return view if isinstance(view, Mapping) else None


# ---------------------------------------------------------------------------
# 渲染 (纯函数)
# ---------------------------------------------------------------------------


def render_trace(data: TraceData, *, show_view: bool = False) -> str:
    """一份轨迹 -> 直接能 print 的多行文本.

    Args:
        data: 读出来的轨迹 (金额与算式都在 run 行里, 不在这里算).
        show_view: 是否追加第二档 (每帧的视图与诊断值).

    Returns:
        str: 多行文本 (末尾**不带**换行 —— 由调用方决定怎么打).
    """
    lines = [*_head(data), *_amount_block(data.run), ""]
    lines.extend(_calls_block(data.calls))
    if show_view:
        lines.append("")
        lines.extend(_frames_block(data.frames))
    return "\n".join(lines)


def _head(data: TraceData) -> list[str]:
    """账目那一段 (五行: 编号 / 归属 / 状态与版本 / 起止 / 用量)."""
    run = data.run
    thread = data.thread
    return [
        f"run  {run.run_id}",
        f"  会话 {run.thread_id} (租户 {_dash(thread.tenant_id if thread else None)}"
        f" / 用户 {_dash(thread.user_id if thread else None)})",
        f"  状态 {run.status} · 模型 {_dash(run.model)}"
        f" · 提示词 {_dash(run.prompt_version)}",
        f"  起止 {_moment(run.created_at)} → {_end(run.finished_at)}"
        f" (耗时 {_elapsed(run.created_at, run.finished_at)})",
        f"  {_tokens_text(run)}",
    ]


def _tokens_text(run: Run) -> str:
    """用量那一行: 输入拆成 cache_hit / cache_miss, output 带上 reasoning.

    三档拆开写而不是只报一个输入总量 —— 这是计价那条口径在屏幕上的兑现: 各自的
    单价差着量级, 合成一个数之后「这一趟大部分输入都是命中的」这句话就没了.
    """
    text = (
        f"token input {_count(run.input_tokens)}"
        f" (cache_hit {_count(run.cache_hit_tokens)}"
        f" / cache_miss {_count(run.cache_miss_tokens)})"
        f" · output {_count(run.output_tokens)}"
    )
    # 推理分量是输出的**明细** (已含在里面, 见 db/cost.py 那条核实): 有就标一下,
    # 没有就整块省掉 —— 「reasoning 0」看起来像「这个模型没思考」, 而真相是没这个数据
    if run.reasoning_tokens is not None:
        text += f" (reasoning {run.reasoning_tokens})"
    return f"{text} · total {run.total_tokens}"


def _amount_block(run: Run) -> list[str]:
    """金额那一段: 库里那两列直接读出来 (算得出来给钱 + 算式, 算不出来给原因).

    算式那一行是这一片的说明书写在屏幕上: 读者自己就能验算, 不必信框架 —— 而算式
    是**当时**算出来存下的, 不是现在补算的 (补算会用今天的价, 那是另一笔钱).
    """
    cost = RunCost.from_detail(run.total_cost_detail)
    if run.total_cost is None:
        # 没有金额: 明细里那句「为什么没有」就是答案 (老行可能连明细都没有)
        reason = _gap_text(cost, run.model)
        return [f"  金额 (没有) {reason}"]
    head = f"  金额 ¥{_number(run.total_cost)}"
    if cost.tier is not None:
        # 峰谷表才有档位: 这笔是按峰价还是谷价算的, 事后要看的就是它
        head += f" ({cost.tier})"
    if not cost.known:
        # 金额在、算式读不出来 (老格式 / 坏数据): 金额是主, 别把它藏起来
        return [head, f"    └ (算式读不出来: {cost.gap.value if cost.gap else '-'})"]
    return [head, f"    └ {_formula(cost)}"]


def _gap_text(cost: RunCost, model: str | None) -> str:
    """算不出来时的那句话 (原因各不同, 修法也各不同).

    措辞在这里, 而**判断**在 `db/cost.py` (那边只说是什么原因): 一个要改部署配置,
    一个要升依赖, 一个要去查上游为什么没上报 —— 合起来说会把排查方向也吞了.
    """
    gap = cost.gap
    if gap is CostGap.BAD_CONFIG:
        return f"价目表没读成: {cost.error or ''}".rstrip()
    if gap is CostGap.NO_PRICE:
        # 明细里那份是算钱那一刻记下的 (更接近当时的事实); 手写的老明细可能没有它,
        # 那就退回运行行上那个模型名 —— 两者正常情况本来就相等
        name = cost.model or model
        if name is None:
            return "未配置单价 (这次运行没记模型名, 查不了价)"
        return f"未配置单价 (模型 {name} 不在当时的价目表里)"
    if gap is CostGap.NO_TOKENS:
        missing = " / ".join(cost.missing) or "?"
        return f"算不出来 (上游没上报这几个分量: {missing})"
    if gap is CostGap.NO_CALENDAR_LIB:
        return "算不出来 (当时没装中国节假日日历, 判不了峰谷)"
    if gap is CostGap.NO_CALENDAR_DATA:
        return f"算不出来 (日历里没有 {cost.date or '那一天'} 所在的年份)"
    if gap is CostGap.NO_MOMENT:
        return "算不出来 (不知道这次运行从哪一刻开始, 判不了峰谷)"
    if gap is CostGap.UNFINISHED:
        return "没跑完, 没有账目"
    if gap is CostGap.NO_DETAIL:
        # 正常状态之一 (那一趟还没收尾 / 改口径之前写的), 别说成坏数据
        return "库里没有这笔账 (这一趟还没收尾, 或它是改口径之前写的)"
    if gap is CostGap.TOO_SMALL:
        return "算出来的钱小于 0.000001 (这一列的最小刻度), 存不下"
    if gap is None:
        # 明细里记着算式, 金额那一列却是空的 —— 这一行自相矛盾 (正常写入不会这样:
        # 两列同生共死). 如实说出来, 别顺手替它算一个
        return "库里这一行自相矛盾 (明细有算式, 金额却是空的)"
    return "算不出来 (库里那份明细读不懂: 老格式或坏数据)"


def _formula(cost: RunCost) -> str:
    """三档算式 (每档: 用量 x 单价/M), 顺序与用量那行一致.

    用输入总量减出来的那一档要标明来路: 用量那行的同一个位置写着 `-` (那一列上游
    确实没报), 不标的话屏幕上会冒出一个「用量是 -, 算式里却有数」的档.
    """
    parts = []
    for label, line, column in (
        ("cache_miss", cost.line_of.get("cache_miss"), "cache_miss_tokens"),
        ("cache_hit", cost.line_of.get("cache_hit"), "cache_hit_tokens"),
        ("output", cost.line_of.get("output"), "output_tokens"),
    ):
        if line is None:
            continue
        # 乘号用 ASCII 的 `x` 而不是全角乘号: ruff 的 RUF001 把后者算作歧义字符
        # (与字母 x 混淆), 本仓的 lint 口径是全仓一致地避开它.
        # 单价后面的 `/M` 是「每百万 token」—— 与 db/cost.py 的单价单位同一个意思
        note = " (推自 input)" if column in cost.derived else ""
        parts.append(f"{label} {line.tokens} x ¥{_number(line.price)}/M{note}")
    return " + ".join(parts)


def _calls_block(calls: Sequence[ToolCall]) -> list[str]:
    """工具调用那张表: 一次调用一行, 结果行挂在它自己那条下面."""
    header = f"工具调用 ({len(calls)} 次)"
    if not calls:
        return [header, "  这次运行没有调用工具 (纯问答, 或还没跑到工具那一步)"]

    cells = [
        {
            "#": str(index),
            "工具名": call.tool_name,
            "状态": call.status,
            "耗时": "-" if call.duration_ms is None else f"{call.duration_ms}ms",
            "参数": _short(call.arguments, _ARG_WIDTH),
        }
        for index, call in enumerate(calls, start=1)
    ]
    widths = {
        column: max(
            display_width(column),
            *(display_width(row[column]) for row in cells),
        )
        for column in _COLUMNS
    }

    def row_text(row: Mapping[str, str]) -> str:
        pieces = []
        for column in _COLUMNS:
            text = row[column]
            pad = " " * (widths[column] - display_width(text))
            # 序号右对齐 (一位数与两位数在同一列上对得齐), 其余左对齐
            pieces.append(pad + text if column in _RIGHT_ALIGNED else text + pad)
        # 刻意不 rstrip: 末列的补白是表格宽度的一部分 (与 checkpoint 的历史表同)
        return _TABLE_INDENT + _SEPARATOR.join(pieces)

    lines = [header, row_text({column: column for column in _COLUMNS})]
    for call, row in zip(calls, cells, strict=True):
        lines.append(row_text(row))
        if call.result is not None:
            shown = _short(_result_text(call.result), _RESULT_WIDTH)
            lines.append(f"{_RESULT_INDENT}└ 结果: {shown}")
    return lines


def _result_text(result: object) -> str:
    """结果 -> 一行文本 (文本原样, 结构化结果转成 JSON).

    `JSONB` 列读回来可能是 dict / list / 数字 —— 直接 `str()` 会打出 Python 的
    单引号写法, 与工具真返回的 JSON 不是一回事.
    """
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def _frames_block(frames: Sequence[FrameView]) -> list[str]:
    """第二档: 每帧一行, 报那一轮真的发出去的是什么 + 两个诊断值."""
    if not frames:
        return [
            "帧视图 (0 帧) —— 这一行没配快照存储 (或这一趟一帧都没落成), 视图只在快照里"
        ]
    lines = [f"帧视图 ({len(frames)} 帧; 每轮真的发出去那份 —— 库里 metadata 的原样)"]
    lines.extend(
        f"  轮 {frame.turn_number}  {_frame_text(frame.view)}" for frame in frames
    )
    return lines


def _frame_text(view: Mapping[str, Any] | None) -> str:
    """一帧的视图 -> 一行 (只写库里真有的键, 老帧少几个键也打得出来)."""
    if view is None:
        # 视图为空有**两种**来路, 而帧里分辨不出来 (视图是「投影的产物」, 没投影
        # 就没它): 没配压缩策略 (框架 CLI 的默认 —— 2026-09-24 真机跑出来就是这样),
        # 或者那一轮压根没有模型调用 (挂起补做). 只说一种会把另一种说成不存在.
        return "没落视图 (没配压缩策略, 或那一轮没有模型调用)"
    parts: list[str] = []
    estimated = view.get("estimated_tokens")
    drift = view.get("estimate_drift")
    if isinstance(estimated, int) and isinstance(drift, int):
        parts.append(f"估算 {estimated} tok (漂移 {drift:+d})")
    elif isinstance(estimated, int):
        parts.append(f"估算 {estimated} tok")
    elif isinstance(drift, int):
        parts.append(f"漂移 {drift:+d}")
    ratio = view.get("cache_hit_ratio")
    if isinstance(ratio, int | float):
        parts.append(f"命中率 {ratio:.2f}")
    if "dropped" in view:
        piece = (
            f"裁掉 {view.get('dropped')} 条 / 截短 {view.get('truncated')} 条 / "
            f"清思维链 {view.get('reasoning_cleared')} 段"
        )
        if "saved_tokens" in view:
            piece += f" (省 {view.get('saved_tokens')})"
        parts.append(piece)
    if view.get("summarized"):
        parts.append("已更新摘要")
    if view.get("emergency"):
        parts.append("紧急压缩")
    if view.get("warning"):
        parts.append(f"警告: {view.get('warning')}")
    if view.get("skipped"):
        parts.append(f"跳过: {view.get('skipped')}")
    messages = view.get("messages")
    if isinstance(messages, list):
        parts.append(f"视图 {len(messages)} 条")
    return " · ".join(parts) if parts else "视图里没有可读的键"


# ---------------------------------------------------------------------------
# 小工具 (取值 -> 一行文本)
# ---------------------------------------------------------------------------


def _dash(value: object) -> str:
    """空值一律打 `-` (不印 None, 也不留空 —— 空着看不出是没记还是没显示)."""
    return "-" if value is None else str(value)


def _count(value: int | None) -> str:
    """token 分量: 没上报打 `-` (与 0 是两回事, 见 db/schema.py 那五列)."""
    return "-" if value is None else str(value)


def _number(value: Decimal | None) -> str:
    """金额 / 单价 -> 人看的小数 (去掉尾随的零, 但不写成科学计数法).

    6 位小数是本片的展示标度 (与 `total_cost` 那一列同标度) —— 不用 4 位: 一次
    小运行的钱可能落在第 5、6 位上, 四舍五入之后会变成 `¥0.0000`, 那正是本片要
    躲开的那个「看起来像个答案的 0」.
    """
    if value is None:
        return "-"
    return format(value.normalize(), "f")


def _moment(value: datetime | None) -> str:
    """时刻 -> `2026-09-24 10:02:11Z` (UTC, 与库里存的一致; 不做本地时区换算)."""
    if value is None:
        return "-"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") + "Z"


def _end(finished_at: datetime | None) -> str:
    """结束那一刻; 还没结束就明说 (拿「现在」顶替会造出一个还在变的耗时)."""
    return _moment(finished_at) if finished_at is not None else "未结束"


def _elapsed(created_at: datetime | None, finished_at: datetime | None) -> str:
    """耗时 (秒, 一位小数); 没结束 -> `-`."""
    if created_at is None or finished_at is None:
        return "-"
    return f"{(finished_at - created_at).total_seconds():.1f}s"


def _short(text: str, width: int) -> str:
    """超长文本截断, 并在尾巴上报出总长.

    只截不留话会让读的人以为「参数就这么多」—— 而这里截掉的正是排查时要看的那
    半句. 报总长之后, 「要不要去库里看全文」由他自己决定.
    """
    if len(text) <= width:
        return text
    return f"{text[:width]}... (共 {len(text)} 字)"


# ---------------------------------------------------------------------------
# 进程入口
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TraceOptions:
    """命令行选项 (run_id 必给; --view 决定要不要读帧).

    attributes:
        run_id: 要看的运行编号.
        show_view: 是否展开每帧的视图 (第二档).
    """

    run_id: str
    show_view: bool = False


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器 (单独一个函数: 帮助文本是对外契约, 好单测)."""
    parser = argparse.ArgumentParser(
        prog="python -m CharAgent.client.trace",
        description=(
            "只读地回看一次运行: 工具调用清单 + 三档用量与金额 "
            "(金额与算式都是收尾那一刻算好写进库的, 这里只读)"
        ),
    )
    parser.add_argument("run_id", help="要看的运行编号 (charagent_runs.run_id)")
    parser.add_argument(
        "--view",
        action="store_true",
        help="第二档: 展开每帧的视图 (那一轮真的发出去的东西) 与估算漂移",
    )
    return parser


def parse_argv(argv: Sequence[str] | None = None) -> TraceOptions:
    """命令行参数 -> TraceOptions (唯一一处把 argv 翻译成配置的地方)."""
    args = build_parser().parse_args(argv)
    return TraceOptions(run_id=args.run_id, show_view=args.view)


def main(
    argv: Sequence[str] | None = None,
    *,
    database: Database | None = None,
    writer: Callable[[str], Any] | None = None,
) -> int:
    """进程入口: 读环境 -> 解析参数 -> 读库 -> 打印.

    Args:
        argv: 命令行参数 (None 表示取 sys.argv[1:]; 测试直接传一个列表).
        database: 数据库入口 (None 表示按环境变量造一个真的); 测试塞一个指向
            测试 schema 的进来 —— 与全仓其他测试同一套注入缝.
        writer: 输出函数 (None 表示 `print`).

    Returns:
        int: 退出码 —— 0 打出来了; 1 编号不存在 / 配置或读库出错; 2 用法错
        (argparse 自己给的).

    Raises:
        (不抛: 配置与读库的错在这里翻成一行人话 + 退出码)
    """
    load_root_env()
    use_utf8_stdio()
    say = print if writer is None else writer
    options = parse_argv(argv)

    # **本入口不读价目表**: 金额在运行收尾那一刻就算好写进库里了 (ticket 28),
    # 这里只把库里那份事实摆出来. 于是它也不需要任何环境变量, 更不碰日历依赖
    owned = database is None
    db = PgDatabase() if database is None else database
    try:
        data = asyncio.run(
            load_trace(db, options.run_id, with_frames=options.show_view)
        )
    except DbError as exc:
        say(f"读取失败: {type(exc).__name__}: {exc}")
        return 1
    finally:
        # 自己建的自己关 (注入进来的可能是别人的池子, 别替它关)
        if owned:
            asyncio.run(db.dispose())

    if data is None:
        say(f"没有这次运行: {options.run_id} (charagent_runs 里没有这个编号)")
        return 1
    say(render_trace(data, show_view=options.show_view))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
