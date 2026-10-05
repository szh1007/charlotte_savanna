"""联网召回的过滤与字段映射.

阈值 0.5 是「严格大于」的判据 —— 恰好 0.5 的条目不该进池子. 字段映射错了则
整条路静默失真: `content` 取错字段, 精排读到空串; 不写 `type=web_search`,
融合时被当成知识库块. 这些都不会抛异常, 只能靠用例钉住.

`TavilyClient` 是在 `_call_tavily_search` 函数体内实例化的, monkeypatch 模块
里的这个名字即可, 不用碰网络.
"""

from __future__ import annotations

from app.rag.query import web_search_service


class _FakeTavilyClient:
    """假 tavily: 记录调用参数, search 返回预置 payload —— 接口只要这一个方法."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    def search(self, query: str, max_results: int = 5) -> dict:
        self.calls.append({"query": query, "max_results": max_results})
        return self.payload


def _patch_tavily(monkeypatch, payload: dict) -> _FakeTavilyClient:
    fake = _FakeTavilyClient(payload)
    monkeypatch.setattr(web_search_service, "TavilyClient", lambda: fake)
    return fake


def test_results_at_half_score_are_filtered_out(monkeypatch):
    """0.5 整不进池子 —— 判据是严格大于, 等于也滤掉; 0.51 才留下."""
    _patch_tavily(
        monkeypatch,
        {
            "results": [
                {
                    "title": "低",
                    "content": "低分内容",
                    "url": "https://low",
                    "score": 0.5,
                },
                {
                    "title": "高",
                    "content": "高分内容",
                    "url": "https://high",
                    "score": 0.51,
                },
            ]
        },
    )

    docs = web_search_service._call_tavily_search("问题")

    assert [doc["title"] for doc in docs] == ["高"]


def test_fields_are_mapped_from_the_tavily_shape(monkeypatch):
    """chunk_id 补空串, type 固定 web_search, content 进 text —— 下游按这个形状消费.

    web 结果没有 chunk_id, 用空串占位; `type` 是融合与精排区分两路召回的依据.
    """
    fake = _patch_tavily(
        monkeypatch,
        {
            "results": [
                {
                    "title": "标题",
                    "content": "正文",
                    "url": "https://example.com/a",
                    "score": 0.8,
                }
            ]
        },
    )

    docs = web_search_service._call_tavily_search("保养问题")

    assert docs == [
        {
            "chunk_id": "",
            "title": "标题",
            "text": "正文",
            "score": 0.8,
            "type": "web_search",
            "url": "https://example.com/a",
        }
    ]
    assert fake.calls == [{"query": "保养问题", "max_results": 5}]


def test_missing_fields_fall_back_to_empty_defaults(monkeypatch):
    """tavily 条目缺字段时不炸, 缺的部分补空串 —— 一个残条目不该毁掉整路召回."""
    _patch_tavily(monkeypatch, {"results": [{"score": 0.9}]})

    docs = web_search_service._call_tavily_search("问题")

    assert docs == [
        {
            "chunk_id": "",
            "title": "",
            "text": "",
            "score": 0.9,
            "type": "web_search",
            "url": "",
        }
    ]


def test_payload_without_results_yields_an_empty_list(monkeypatch):
    """tavily 没给 results 时返回空列表, 而不是 None —— 调用方按列表迭代."""
    _patch_tavily(monkeypatch, {})

    assert web_search_service._call_tavily_search("问题") == []
