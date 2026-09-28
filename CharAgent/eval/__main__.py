"""`python -m CharAgent.eval` —— 对照两份跑分报告.

一句话理解: 一个 **只看文件的**小命令 —— 读两份 JSON, 把差异表打在屏幕上 (或落成
一份 .md).

为什么只有一个子命令: 跑分本身要**业务侧的装配** (造会话 / 给题集 / 铺假端点),
框架给不出一个通用的 `run`. 跑分由业务侧的入口落成 JSON (issue 44), 框架这一页
只负责「拿两份 JSON 比一比」—— 那件事与业务无关, 谁都能用.

用法::

    python -m CharAgent.eval compare 改前.json 改后.json
    python -m CharAgent.eval compare a.json b.json --group-a 全挂 --group-b 裁剪
    python -m CharAgent.eval compare a.json b.json -o 对照.md
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from CharAgent.eval.compare import compare_files
from CharAgent.eval.utils.errors import EvalError


def build_parser() -> argparse.ArgumentParser:
    """命令行参数 (只一个子命令, 但照子命令写 —— 以后要加别的不用改形状)."""
    parser = argparse.ArgumentParser(
        prog="python -m CharAgent.eval",
        description="跑分报告的对照工具 (只读文件, 不跑模型)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    compare = sub.add_parser("compare", help="读两份 JSON 报告, 出差异表")
    compare.add_argument("path_a", help="第一份报告 (JSON)")
    compare.add_argument("path_b", help="第二份报告 (JSON)")
    compare.add_argument(
        "--group-a",
        default=None,
        help="第一份里挑哪一组 (不写 = 要求它只有一组)",
    )
    compare.add_argument(
        "--group-b",
        default=None,
        help="第二份里挑哪一组 (不写 = 要求它只有一组)",
    )
    compare.add_argument(
        "-o",
        "--output",
        default=None,
        help="写进这个文件 (不写 = 打到屏幕上)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """命令入口: 解析参数 → 比 → 打到屏幕或落盘.

    Returns:
        int: 进程退出码 (0 正常; 1 参数错 / 报告读不了 —— 都是「去改输入」这一类).
    """
    args = build_parser().parse_args(argv)
    try:
        text = compare_files(
            args.path_a,
            args.path_b,
            group_a=args.group_a,
            group_b=args.group_b,
        )
    except EvalError as exc:
        print(f"[错] {exc}", file=sys.stderr)
        return 1
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"[好] 差异表已写入 {args.output}")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
