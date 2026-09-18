"""客服提示词: 它在业务目录里、按版本落盘, 而且该说的四条禁则都在.

提示词是这个助手的立身之本 —— 工具决定它**能**做什么, 提示词决定它**不会**做
什么. 后半句没法用单元测试断言 (那要靠评估集, L4 的事), 但下面两件事可以:

1. **落库方式**: 按 `{名字}/{版本}.prompt` 分目录存 (PLAN §3.3) —— 换一版是加一个
   文件, 不是覆盖旧文件.
2. **该写的写没写**: 四条禁则、项目术语、示例.

为什么钉的是「必须有哪几件事」而不是逐字比对全文: 话术会改, 改话术不该红;
但「不能替买家付款」这类禁则被删掉, 必须红.
"""

from __future__ import annotations

import pytest

from CharAgent.prompt import PromptNotFoundError, load_prompt
from CharApp.minimall.cli import PROMPT_DIR, PROMPT_NAME, PROMPT_VERSION

# 当前声明的这一版在盘上的位置 (好几条用例都要它, 只算一次)
CURRENT_PROMPT = PROMPT_DIR / PROMPT_NAME / f"{PROMPT_VERSION}.prompt"


def system_prompt() -> str:
    """按命令行那条路读一遍提示词 (读法必须与生产一致)."""
    return load_prompt(f"{PROMPT_NAME}/{PROMPT_VERSION}", prompt_dir=PROMPT_DIR)


# ---------------------------------------------------------------------------
# 落库方式与版本
# ---------------------------------------------------------------------------


def test_the_prompt_lives_in_a_versioned_layout() -> None:
    """提示词按 `{名字}/{版本}.prompt` 落盘, 而且就在**业务自己**的目录下.

    布局带版本的理由是评估: 要比「这一版比上一版好」, 前提是两版都在. 覆盖式布局
    会静默地把上一版弄丢, 那次对比就永远做不成 (PRD §4.8).
    """
    assert CURRENT_PROMPT.is_file()
    assert sorted(path.name for path in PROMPT_DIR.iterdir()) == [PROMPT_NAME]
    assert sorted(path.name for path in (PROMPT_DIR / PROMPT_NAME).iterdir()) == [
        f"{PROMPT_VERSION}.prompt"
    ]


def test_the_declared_version_is_the_one_that_gets_loaded() -> None:
    """声明用哪一版, 读到的就是哪一版 —— 声明与落盘对不上时当场炸.

    这条守的是「版本」这件事的意义: 评估结论要能归因到具体一版. 若读不到就悄悄
    退回上一版, 一次「v2 的跑分」可能其实是 v1 的成绩, 而且没有任何地方会报警.
    `PromptNotFoundError` 是启动期错误, 命令行会报一句人话就退出.
    """
    assert system_prompt() == CURRENT_PROMPT.read_text(encoding="utf-8")

    with pytest.raises(PromptNotFoundError):
        load_prompt(f"{PROMPT_NAME}/v999", prompt_dir=PROMPT_DIR)


# ---------------------------------------------------------------------------
# 内容
# ---------------------------------------------------------------------------


def test_the_prompt_is_read_from_the_business_directory() -> None:
    """读得到, 且读出来的是一份客服人设 (不是框架那份占位文本)."""
    body = system_prompt()

    assert len(body) > 200, "提示词短得不像一份客服人设, 八成读错了文件"
    assert "客服" in body


def test_the_prompt_forbids_the_four_things_it_must_forbid() -> None:
    """四条禁则一条都不能少 (写操作 / 查别人 / 编造 / 许诺)."""
    body = system_prompt()

    for must_say in (
        "下单",  # 不能替买家付款、下单、取消、退款 (L1a 只有查询)
        "不能查别人的",
        "不编造",
        "不许诺",
    ):
        assert must_say in body, f"提示词里少了这条禁则: {must_say}"


def test_the_prompt_uses_the_project_vocabulary() -> None:
    """术语按 CONTEXT.md 用: 取消 ≠ 退款, 且**不存在卖家角色**.

    这三条在 PRD §7.2 里被点名为最容易写错的 —— 写错了模型就会对买家说出商城里
    并不存在的东西 (比如「我帮你联系商家」).

    说清楚这条用例的边界: 它只断「这几个词在不在」, **断不了「用的是不是对的
    意思」** (让模型去做退货退款, 这里照样绿). 语义对不对要靠 L4 的评估集, 不是
    单元测试能兜的 —— 所以这几行不是配方的全部, 只是防「整段被删掉」.
    """
    body = system_prompt()

    assert "取消" in body and "退款" in body
    assert "退货退款" in body, "要明确说清商城不涉及寄回, 否则模型会自己编一套退货流程"
    assert "没有卖家" in body


def test_the_prompt_carries_examples() -> None:
    """示例直接写在模板里 (PRD §4.8: 第一阶段不做示例检索, 那是跟知识库一起做的事)."""
    body = system_prompt()

    assert "买家:" in body and "回答:" in body, "示例要写进模板, 且看得出是示例"
