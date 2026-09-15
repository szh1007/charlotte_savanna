"""client 包支撑子包: 启动选项与交互命令的静态零件 (issue 10).

与本仓其他包的 `utils/` 定位一致: 行为 (解析参数 / 跑会话 / 画终端) 在包顶层的
app / session / render 三个模块里, 纯数据结构与纯函数集中于此供它们共享.

- types.py      CliOptions: 解析完 argv 后装启动选项的那个对象
- commands.py   交互命令 (以 / 开头的行) 的枚举与解析纯函数

域内常量跟随其所属类型 (如默认会话编号在 types.py、命令前缀在 commands.py),
不上浮到这里.
"""

from __future__ import annotations
