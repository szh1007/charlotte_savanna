"""stream 包异常语义 (issue 05 / #4 事件状态机).

对齐 model/utils/errors.py 与 tool/utils/errors.py 的错误族组织 (纯 Exception
基类):
- StreamError: 本包错误基类.
- EventSequenceError: 事件序列不变量被破坏 (终局后再发事件 / 工具结果与调用
  不配对 / 工具未回填完就终局). 这类错误不是外部输入错误, 而是 loop 接线
  的编程错误 —— 状态机在事件产出的瞬间拦下, 而不是让前端收到乱序事件流.

大白话版:
- 这是「喊话不守规矩」这种错误的定义: 顺序乱了 (还没说要查就喊查回来了 /
  喊了答完了又喊别的 / 工具结果还没回就喊答完了) 就抛它.
- 它不是用户输入错了, 而是框架自己接线接错了 —— 所以要在事情发生的瞬间
  立刻报出来, 而不是让乱糟糟的直播画面推给用户.
"""

from __future__ import annotations


class StreamError(Exception):
    """stream 包错误基类 (纯 Exception: 本包错误均为框架接线期的编程错误)."""


class EventSequenceError(StreamError):
    """事件序列不变量被破坏 (03-api.md §2 状态机约束, 面向开发者)."""
