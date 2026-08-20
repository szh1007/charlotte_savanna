"""LLM JSON 解析容错单元测试 (bugfix: LLM 偶发多 JSON 拼接 / 尾部多余文本).

覆盖 _parse_json: 正常 / 代码块包裹 / Extra data (多 JSON 拼接) /
JSON + 尾随文本 / 完全非法 / 非对象结构.
"""

from __future__ import annotations

import pytest
from backend import llm

VALID = {
    "title": "t",
    "chapters": [{"start": 0.0, "end": 10.0, "title": "ch", "points": ["p1"]}],
}


def test_parse_valid_json() -> None:
    assert llm._parse_json('{"title": "t"}') == {"title": "t"}


def test_parse_fenced_code_block() -> None:
    """LLM 用 markdown 代码块包裹 JSON 时仍可解析."""
    content = '```json\n{"title": "t"}\n```'
    assert llm._parse_json(content) == {"title": "t"}


def test_parse_extra_data_concatenated_json() -> None:
    """多 JSON 拼接 (用户反馈: Extra data 报错) → 取第一个对象."""
    content = '{"title": "t"}{"title": "second"}'
    assert llm._parse_json(content) == {"title": "t"}


def test_parse_json_with_trailing_text() -> None:
    """JSON 后跟多余文本 (换行 + 解释) → 取第一个对象."""
    content = '{"title": "t"}\n\n以上是总结内容, 请查看'
    assert llm._parse_json(content) == {"title": "t"}


def test_parse_brackets_inside_string() -> None:
    """对象内部的括号 (字符串内) 不干扰平衡扫描."""
    content = '{"title": "a {b} c", "chapters": []}\n 结尾'
    assert llm._parse_json(content) == {"title": "a {b} c", "chapters": []}


def test_parse_completely_invalid_raises() -> None:
    with pytest.raises(llm.LLMError, match="非法 JSON"):
        llm._parse_json("完全没有 JSON")


def test_parse_non_object_raises() -> None:
    with pytest.raises(llm.LLMError, match="结构非法"):
        llm._parse_json("[1, 2, 3]")
