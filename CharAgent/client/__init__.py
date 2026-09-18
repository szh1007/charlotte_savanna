"""client 包: 命令行演示入口 `python -m CharAgent.client` (P0 整体验收线).

一句话理解: 这是「不依赖 server 也能把框架跑起来」的那扇门 —— 终端里问一句,
agent 带着工具去查去算, 过程实时打在屏幕上, 中途 Ctrl-C 能打断, 再 `/resume`
(或直接说一句「继续」) 就从断点接着跑. 九个包在这里第一次被装配成一台真的
机器 (此前只有测试在组装它们).

设计依据:
- P0 验收: CLI 跑通带工具问答 + checkpoint 中断续跑 + `pytest tests/` 全绿
- difficulties #3 (kill switch = `asyncio.Task.cancel` 即时打断) / #4 (事件流实时
  展示) / #5 (断点续跑不重复已完成动作), 外加快照存储配置可切换
- 与本仓其他包同一套组织: 行为模块在顶层, 静态零件收在 utils/

结构总览:
- app.py      进程入口: 参数解析 + 装配 (模型 / 存储 / 会话) + 常驻事件循环 +
              交互与一次性两条路 (Ctrl-C 打断也在这里, 见 _KillSwitch)
- session.py  ChatSession: 一次会话的三个动作 —— 问一句 / 接着跑 / 看存档
- render.py   终端渲染: 六类事件各画一行, 外加结果摘要与答复正文
- utils/      支撑子包: types (CliOptions) + commands (交互命令解析)

三条通道在本包汇聚 (与框架侧的划分一致):
- 事件流 -> `render.EventPrinter` (走 EventSink 出口, 与 P1 推 SSE 同一条协议)
- 对话历史 -> `session.ChatSession` (wire 消息, 多轮连得上)
- 快照 -> `checkpoint_saver_from_env` 挑的实现 (换后端只改一个配置)

用法::

    python -m CharAgent.client                        # 交互模式 (默认内存快照)
    python -m CharAgent.client -q "现在几点?"          # 问一句就退出
    python -m CharAgent.client --backend postgres     # 换快照后端
    python -m CharAgent.client --history              # 看本会话的快照历史表

模块内部 import 走具体模块路径 (client.app, client.session), 不绕包门面; 对外公共 API
统一由本文件 __all__ 导出.
"""

from __future__ import annotations

from CharAgent.client.app import (
    PROMPT,
    build_model,
    build_parser,
    build_saver_for,
    main,
    parse_argv,
)
from CharAgent.client.render import (
    EventPrinter,
    format_answer,
    format_outcome,
    format_result,
)
from CharAgent.client.session import DEMO_TOOLS, ChatSession
from CharAgent.client.utils.commands import Command, looks_like_command, parse_command
from CharAgent.client.utils.types import DEFAULT_THREAD_ID, CliOptions

__all__ = [
    "DEFAULT_THREAD_ID",
    "DEMO_TOOLS",
    "PROMPT",
    "ChatSession",
    "CliOptions",
    "Command",
    "EventPrinter",
    "build_model",
    "build_parser",
    "build_saver_for",
    "format_answer",
    "format_outcome",
    "format_result",
    "looks_like_command",
    "main",
    "parse_argv",
    "parse_command",
]
