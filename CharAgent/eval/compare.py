"""对照入口: 读两份落盘的 JSON 报告, 出一张差异表.

一句话理解: **「这次改得比上次好」最终就是这张表** —— 跑分落了盘, 隔一天改了
prompt 再跑一遍, 两边的 JSON 拿来比一比.

两种比法 (同一段代码):

| 比什么 | 两份报告长什么样 | 什么时候用 |
|--------|----------------|-----------|
| 同一组的两轮之间 | 各含一组, 组名相同 | 改了 prompt / 改了工具集, 与上一版比 |
| 两组之间 | 各含一组 (组名不同), 或一份含两组 | 一次 A/B 的两个臂 |

**为什么要按「组」挑**: 一份报告可以含两组 (一次跑两臂), 而对照是逐对做. 于是
`group_a` / `group_b` 两个参数说清「拿哪一组与哪一组比」; 不写时要求每份报告只有
一组 —— 含糊地拿第一组去比, 比出来的东西没人说得清是哪两组的差.

渲染那三块 (总表 / 差异归因 / 两类收益) 走的是 `report.render_comparison`, 与报告
里的那三块**是同一条路** —— 于是文件对照不会长成另一副面孔.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from CharAgent.eval.report import load_report, render_comparison
from CharAgent.eval.utils.errors import EvalConfigError


def compare_files(
    path_a: str,
    path_b: str,
    *,
    group_a: str | None = None,
    group_b: str | None = None,
) -> str:
    """读两份报告文件, 交一张差异表 (Markdown).

    Args:
        path_a: 第一份 JSON (对照表里那一列的表头用它的组名).
        path_b: 第二份 JSON.
        group_a: 从第一份里挑哪一组; None = 要求它只有一组 (挑不出就报错).
        group_b: 同上, 对第二份.

    Returns:
        str: 差异表的 Markdown (总表 + 差异归因 + 两类收益).

    Raises:
        EvalConfigError: 文件读不了 / 形状不对 / 版本对不上 / 挑不出唯一的一组.
    """
    return compare_reports(
        load_report(path_a),
        load_report(path_b),
        group_a=group_a,
        group_b=group_b,
    )


def compare_reports(
    data_a: Mapping[str, Any],
    data_b: Mapping[str, Any],
    *,
    group_a: str | None = None,
    group_b: str | None = None,
) -> str:
    """两份**读好的**报告 → 差异表 (给已经拿在手上的调用方用).

    Args:
        data_a / data_b: `report_to_dict` 形状 (或从 JSON 读回来的同一形状).
        group_a / group_b: 挑哪一组; None = 要求那份报告只有一组.

    Returns:
        str: 差异表.

    Raises:
        EvalConfigError: 挑不出唯一的一组 (组名不存在 / 没给组名而报告里不止一组).
    """
    left = pick_group(data_a, group_a)
    right = pick_group(data_b, group_b)
    return render_comparison(left, right)


def pick_group(data: Mapping[str, Any], name: str | None = None) -> Mapping[str, Any]:
    """从一份报告里挑出一组.

    Args:
        data: 报告 (顶层).
        name: 要哪一组; None = 这份报告只有一组时自动取它.

    Returns:
        Mapping: 那一组.

    Raises:
        EvalConfigError: 没有这一组 / 没给组名而报告里不止一组 (报错里列出可选
            的名字 —— 这种错一看就知道该改什么).
    """
    groups: Sequence[Mapping[str, Any]] = list(data.get("groups") or ())
    if not groups:
        raise EvalConfigError("这份报告一组都没有")
    if name is None:
        if len(groups) > 1:
            raise EvalConfigError(
                "这份报告有 "
                f"{len(groups)} 组, 得说清比哪一组: "
                f"{', '.join(str(item.get('name')) for item in groups)}"
            )
        return groups[0]
    for item in groups:
        if item.get("name") == name:
            return item
    raise EvalConfigError(
        f"这份报告里没有 {name!r} 这一组, 实际有: "
        f"{', '.join(str(item.get('name')) for item in groups)}"
    )
