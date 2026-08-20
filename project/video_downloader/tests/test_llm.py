"""LLM 单元测试: JSON 解析容错 + 流式调用 (ADR-0007/0008).

覆盖 _parse_json 容错 (正常 / 代码块包裹 / Extra data / 尾随文本 / 非法 /
非对象) 与 _chat_stream 流式增量 (空块跳过 / 顺序拼接 / close 调用 /
错误透传) 及 summarize_stream / ask_stream / parse_summary_text
(Markdown 总结文档解析, ADR-0008).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from backend import llm


class FakeStream:
    """伪 LLM 流: 可迭代 chunk + close 标记 (模拟 openai SDK stream 对象)."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self) -> None:
        self.closed = True


def _chunk(content: str | None, with_choices: bool = True) -> SimpleNamespace:
    """构造单个流 chunk; content 为 None 或 choices 为空模拟 usage 尾块."""
    if not with_choices:
        return SimpleNamespace(choices=[])
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
    )


def _fake_client(stream: FakeStream) -> SimpleNamespace:
    """构造伪 openai client (chat.completions.create 返回给定流)."""
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: stream))
    )


def test_chat_stream_yields_deltas_and_skips_empty(monkeypatch) -> None:
    """流式增量: 拼接非空 delta, 跳过 usage 尾块与空内容块, close 被调用."""
    stream = FakeStream(
        [
            _chunk("你好"),
            _chunk(None),
            _chunk("世界"),
            _chunk(None, with_choices=False),
            _chunk(""),
        ]
    )
    monkeypatch.setattr(llm, "_client", lambda: _fake_client(stream))
    assert "".join(llm._chat_stream([{"role": "user", "content": "hi"}])) == "你好世界"
    assert stream.closed


def test_chat_stream_passes_stream_flag(monkeypatch) -> None:
    """create 收到 stream=True 与 model (ADR-0008: 不再有 json_mode 分支)."""
    seen: dict = {}

    def _create(**kw):
        seen.update(kw)
        return FakeStream([_chunk("{}")])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )
    monkeypatch.setattr(llm, "_client", lambda: client)
    list(llm._chat_stream([{"role": "user", "content": "hi"}]))
    assert seen["stream"] is True
    assert "response_format" not in seen
    assert seen["model"] == llm.config.LLM_MODEL


def test_chat_stream_raises_llm_error(monkeypatch) -> None:
    """创建流失败 → LLMError 透传 (语义同 _chat, 原因明确不猜测)."""

    def _boom(**kw):
        raise RuntimeError("网络错误")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_boom))
    )
    monkeypatch.setattr(llm, "_client", lambda: client)
    with pytest.raises(llm.LLMError, match="网络错误"):
        list(llm._chat_stream([{"role": "user", "content": "hi"}]))


def test_summarize_stream_yields_text(monkeypatch) -> None:
    """summarize_stream: 透传 _chat_stream 增量, prompt 注入 Markdown 模板."""
    seen: dict = {}

    def _fake(messages):
        seen["messages"] = messages
        yield "# 视频总结: t\n"

    monkeypatch.setattr(llm, "_chat_stream", _fake)
    out = list(llm.summarize_stream("转录文本", {"title": "t", "duration": 60}))
    assert "".join(out) == "# 视频总结: t\n"
    assert "## 章节时间线" in seen["messages"][1]["content"]  # 模板注入 (ADR-0008)
    assert "转录文本" in seen["messages"][1]["content"]


def test_ask_stream_yields_text_without_strip(monkeypatch) -> None:
    """ask_stream: 增量原样透传 (不逐块 strip, 保留 chunk 边界空格)."""

    def _fake(messages):
        yield "你好 "
        yield "世界"

    monkeypatch.setattr(llm, "_chat_stream", _fake)
    assert "".join(llm.ask_stream("转录", {"key_points": []}, "问题")) == "你好 世界"


# 完整模板示例 (ADR-0008): 解析结果与 test_summarize.FAKE_SUMMARY 同构
FAKE_SUMMARY_MD = """# 视频总结: 测试视频标题
> 时长: 60s

## 视频概述
视频总结功能的核心流程

## 章节时间线
### 核心流程 (00:00 ~ 01:00)
- 字幕优先
- 无字幕时转写

## 核心要点
- 字幕优先
- SenseVoice 转写兜底

## 结论
总结流程闭环"""

FAKE_SUMMARY_DICT = {
    "title": "测试视频标题",
    "overview": "视频总结功能的核心流程",
    "chapters": [
        {
            "start": 0.0,
            "end": 60.0,
            "title": "核心流程",
            "points": ["字幕优先", "无字幕时转写"],
        }
    ],
    "key_points": ["字幕优先", "SenseVoice 转写兜底"],
    "conclusion": "总结流程闭环",
}


def test_parse_summary_markdown_full() -> None:
    """完整模板解析: 章节时间戳 / 要点 / 概述 / 结论 / 标题前缀剥离."""
    assert llm.parse_summary_text(FAKE_SUMMARY_MD) == FAKE_SUMMARY_DICT


def test_parse_summary_markdown_missing_sections_tolerated() -> None:
    """部分小节缺失容忍空值 (仅章节时间线缺失判非法, ADR-0008)."""
    text = "# 视频总结: t\n## 章节时间线\n### 无时间戳章节\n- 要点"
    assert llm.parse_summary_text(text) == {
        "title": "t",
        "overview": "",
        "chapters": [
            {"start": 0.0, "end": 0.0, "title": "无时间戳章节", "points": ["要点"]}
        ],
        "key_points": [],
        "conclusion": "",
    }


def test_parse_summary_markdown_missing_chapters_raises() -> None:
    """缺「章节时间线」小节 → LLMError (可重试, 对齐导图缺 chapters)."""
    with pytest.raises(llm.LLMError, match="缺少章节时间线"):
        llm.parse_summary_text("## 视频概述\n只有概述")


def test_parse_summary_markdown_ts_variants() -> None:
    """时间戳格式变体: 中文括号 / 分不补零 / 要点 * 前缀 / 概述多行合并."""
    text = (
        "# 总结: t\n"
        "## 视频概述\n第一行\n第二行\n"
        "## 章节时间线\n"
        "### 章节一（0:05 ~ 2:03）\n* 要点a\n"  # noqa: RUF001 (全角括号为有意用例)
        "### 章节二 1:00 ~ 3:00\n- 要点b"
    )
    data = llm.parse_summary_text(text)
    assert data["title"] == "t"  # "总结:" 前缀同样剥离
    assert data["overview"] == "第一行\n第二行"
    assert data["chapters"][0] == {
        "start": 5.0,
        "end": 123.0,
        "title": "章节一",
        "points": ["要点a"],
    }
    # 无括号包裹的时间戳不解析: 标题整体保留, 时间戳 0.0
    assert data["chapters"][1] == {
        "start": 0.0,
        "end": 0.0,
        "title": "章节二 1:00 ~ 3:00",
        "points": ["要点b"],
    }


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
