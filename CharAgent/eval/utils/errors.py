"""eval 包异常语义 (对齐其他包的纯 Exception 错误族).

三个类, 按「谁该动手」分开:

- `EvalError`: 本包错误基类.
- `EvalConfigError`: 跑分参数写错了 (跑次 < 1 / 一道题都没有 / 组数超过两组 /
  要对照的两份报告对不上). 这类错误的修法是**改调用方那几行**.
- `EvalStartupError`: 开跑前那次自检没过 —— 连第一个 subject 都装配不起来 (多半
  是缺 API key / 缺依赖). 这类错误的修法是**去配环境**.

**判据抛错不在这里**: `Judge` 抛的异常是**一条数据**(记进报告的 `judge_errors`
继续跑), 不是本包的异常 —— 一道题上的判据崩了不该带走整批 60 次运行.

大白话版: `EvalConfigError` 是「你这参数给错了」, `EvalStartupError` 是「你这机器
还没准备好」. 两个都不该等跑到一半才说.
"""

from __future__ import annotations


class EvalError(Exception):
    """eval 包错误基类."""


class EvalConfigError(EvalError):
    """跑分参数不合法 (跑次 / 题集 / 组数 / 报告配对), 面向调用方."""


class EvalStartupError(EvalError):
    """开跑前自检没过: 第一个 subject 就装配不起来 (缺 key / 缺依赖)."""
