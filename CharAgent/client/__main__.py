"""`python -m CharAgent.client` 的模块入口 (issue 10).

为什么单独一个文件: `python -m 包名` 要求包里有个 `__main__.py` 当入口, 而
`client/__init__.py` 的门面是给「import 进来用」的 (`from CharAgent.client import
ChatSession`). 两件事分开写, 各自单纯:

- `__init__.py`  被 import 时不跑任何东西 (没有副作用)
- `__main__.py`  只在「当成脚本跑」时才执行 —— 也就是下面这三行

`raise SystemExit(main())` 而不是 `sys.exit(...)`: 两者等价, 前者不用 import sys,
且一眼看出「这里就是进程的出口」; main 返回的退出码原样传给 shell (0 正常 /
1 配置错或没答完 / 130 被 Ctrl-C 打断).
"""

from __future__ import annotations

from CharAgent.client.app import main

raise SystemExit(main())
