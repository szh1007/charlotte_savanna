"""检索源测试 (Issue 07, SPEC §7.1 可插拔源抽象).

覆盖: 按配置构建 (Tavily 无 key 降级 / 材料非空才挂文档源) / 文档源
关键词命中与回退 / Context7 响应切分与来源 URL 提取 / 知识库预留源空.
不触网: Context7/Tavily 仅测纯逻辑部分.
"""

from project.charplot.pipeline.sources import build_sources
from project.charplot.pipeline.sources.base import SOURCE_DOCS, SOURCE_DOCUMENT
from project.charplot.pipeline.sources.context7_source import Context7Source
from project.charplot.pipeline.sources.document_source import DocumentSource


def test_build_sources_without_tavily_key(monkeypatch):
    """无 TAVILY key → 网络源降级跳过, Context7/文档源保留."""
    from project.charplot.api import config

    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    sources = build_sources(material_text="材料内容")
    names = [s.name for s in sources]
    assert "web" not in names
    assert "docs" in names
    assert "document" in names


def test_build_sources_with_tavily_key(monkeypatch):
    from project.charplot.api import config

    monkeypatch.setattr(config, "TAVILY_API_KEY", "tvly-test")
    sources = build_sources(material_text="材料")
    assert "web" in [s.name for s in sources]


def test_build_sources_skips_document_without_material(monkeypatch):
    from project.charplot.api import config

    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    names = [s.name for s in build_sources(material_text="")]
    assert "document" not in names


def test_document_source_keyword_hits():
    text = (
        "第一章 闭包\n\n闭包是词法作用域的实现\n\n第二章 装饰器\n\n装饰器用于包装函数"
    )
    source = DocumentSource(text)
    results = source.search("装饰器 用法")
    # 命中装饰器段 (关键词命中优先)
    assert results
    assert "装饰器" in results[0].content
    assert results[0].source_type == SOURCE_DOCUMENT


def test_document_source_fallback_to_head():
    source = DocumentSource("第一段内容\n\n第二段内容")
    results = source.search("不存在的关键词xyz")
    assert results
    assert "第一段内容" in results[0].content


def test_context7_split_snippets_extracts_source_url():
    lib = {"id": "/pallets/flask", "title": "Flask"}
    text = (
        "### Standard Routing\n\n"
        "Source: https://github.com/pallets/flask/blob/main/docs/quickstart.md\n\n"
        "The default routing approach.\n" + "-" * 32 + "\n"
        "### Another Snippet\n\nSource: https://flask.palletsprojects.com/\n\nContent B"
    )
    results = Context7Source()._split_snippets(lib, text)
    assert len(results) == 2
    assert results[0].url.startswith("https://github.com/")
    assert results[0].source_type == SOURCE_DOCS
    assert results[1].url == "https://flask.palletsprojects.com/"
