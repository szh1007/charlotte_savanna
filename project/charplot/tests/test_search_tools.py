"""检索源 → LangChain 工具封装测试 (agents/ 的最小覆盖).

钉住一处易踩的坑: build_search_tools 用默认参数显式绑定闭包 (``_src=src``),
规避循环变量捕获 —— 不绑定的话所有工具都会指向最后一个源 (检索悄悄走错源,
只在运行期看结果才发现). 真实 DeepAgents 执行路径 (需真模型且输出非确定)
不在覆盖范围, 属「核实后不做」.
"""

from project.charplot.agents.tools import build_search_tools
from project.charplot.pipeline.sources.base import SearchResult


class FakeSource:
    """最小检索源 (符合 SearchSource 协议: name/description/search)."""

    def __init__(self, name: str, hits: list[SearchResult]):
        self.name = name
        self.description = f"{name} 检索"
        self.hits = hits
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        self.queries.append(query)
        return self.hits


def test_build_search_tools_binds_each_source():
    web = FakeSource(
        "web", [SearchResult(title="w", url="https://x", content="网络片段")]
    )
    docs = FakeSource(
        "docs", [SearchResult(title="d", content="文档片段", source_type="docs")]
    )

    tools = build_search_tools([web, docs])

    assert [t.name for t in tools] == ["web_search", "docs_search"]
    assert [t.description for t in tools] == ["web 检索", "docs 检索"]

    # 各自打到正确的源: 循环变量捕获会让两个工具都指向最后一个源
    assert tools[0].invoke({"query": "装饰器"}) == [
        {"title": "w", "url": "https://x", "content": "网络片段", "source_type": "web"}
    ]
    assert web.queries == ["装饰器"]
    assert docs.queries == []

    hits = tools[1].invoke({"query": "闭包"})
    assert hits == [
        {"title": "d", "url": "", "content": "文档片段", "source_type": "docs"}
    ]
    assert docs.queries == ["闭包"]
    assert web.queries == ["装饰器"]


def test_build_search_tools_empty_sources():
    assert build_search_tools([]) == []
