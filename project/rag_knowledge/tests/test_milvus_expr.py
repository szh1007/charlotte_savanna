"""C02 回归: Milvus 过滤表达式的构造, 与 hybrid_search 的返回值契约.

两件事:
1. `build_in_expr` 把「引号用哪种 + 值怎么转义」定死, 不再依赖 Python list repr
   挑引号的启发式。**注意这不是在修一个已复现的 bug** —— 实测 Milvus 单双引号都收,
   原写法也没试出被拒的取值; 它的性质是防御性加固, 理由写在 `build_in_expr` 的
   docstring 里。
2. `hybrid_search` 失败时此前返回 `None`, 而三个调用方一律 `response[0]` ——
   崩在一个不相干的 TypeError 上, 真正的错因 (Milvus 抖动) 被盖住.
"""

from __future__ import annotations

from app.shared.clients.milvus_utils import hybrid_search
from app.shared.utils.escape_milvus_string_utils import (
    build_in_expr,
    escape_milvus_string,
)


def test_in_expr_uses_double_quotes():
    """引号由本函数定死 (不用 repr 挑), 同一批值每次拼出来都一样."""
    assert build_in_expr("item_name", ["华为", "小米"]) == (
        'item_name in ["华为", "小米"]'
    )


def test_in_expr_escapes_embedded_quotes_and_newlines():
    """值来自文档标题, 是外部内容 —— 带引号/换行不能把表达式解析坏."""
    expr = build_in_expr("item_name", ['HAK"180', "第一行\n第二行"])

    assert expr == 'item_name in ["HAK\\"180", "第一行 第二行"]'


def test_single_value_still_builds_a_list():
    assert build_in_expr("item_name", ["只有一个"]) == 'item_name in ["只有一个"]'


def test_escape_leaves_ordinary_names_alone():
    assert escape_milvus_string("HAK_180烫金机") == "HAK_180烫金机"


# ---------------------------------------------------------------------------
# hybrid_search 的返回值契约
# ---------------------------------------------------------------------------


class _BoomClient:
    """一调就炸的假 Milvus 客户端."""

    def hybrid_search(self, **kwargs):
        raise RuntimeError("Milvus 抖动")


class _FakeClient:
    """返回 pymilvus 那种「每个 query 一份命中」的外层列表."""

    def hybrid_search(self, **kwargs):
        return [[{"id": 1}, {"id": 2}]]


def test_hybrid_search_returns_empty_list_on_failure_not_none():
    """回归本体: 失败必须返回空列表 —— 返回 None 会让下游崩在 [0] 上."""
    result = hybrid_search(_BoomClient(), collection_name="c", reqs=[])

    assert result == [], "失败应当返回空列表, 而不是 None"


def test_hybrid_search_unwraps_the_per_query_dimension():
    """每个请求只发一个 query, 所以在这里一次拆掉外层维度."""
    result = hybrid_search(_FakeClient(), collection_name="c", reqs=[])

    assert result == [{"id": 1}, {"id": 2}]
