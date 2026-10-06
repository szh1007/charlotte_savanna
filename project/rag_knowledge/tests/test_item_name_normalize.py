"""主体名归一化: 规范名与口语名的差异该用规则判, 不该靠向量分数.

实测背景 (C17 扩建题库时量的): 库里存的是「中华人民共和国数据安全法」, 用户说的是
「数据安全法」—— 向量相似度只有 **0.701**, 而阈值是 0.70, 刚好压线; 同时**错误的**
候选能到 0.68. 右侧只差 0.02, 左侧只差 0.001 —— 这种间隔经不起模型版本或语料变动.

「差一个『中华人民共和国』前缀」是个**确定的**事实, 用一条规则判掉, 相似度就只用来
分辨真正不同的文档了.
"""

from __future__ import annotations

from app.rag.query.item_name_confirm_service import (
    _pick_inferred_item_name,
    normalize_item_name,
)


def test_strips_the_republic_prefix():
    assert normalize_item_name("中华人民共和国数据安全法") == "数据安全法"


def test_short_name_stays_as_is():
    assert normalize_item_name("数据安全法") == "数据安全法"


def test_strips_book_title_marks():
    assert normalize_item_name("《个人信息保护法》") == "个人信息保护法"


def test_ignores_inner_spaces():
    """法规页里的标题有「中华人民共和国 网络安全法」这种带空格的写法."""
    assert normalize_item_name("中华人民共和国 网络安全法") == "网络安全法"


def test_only_the_republic_prefix_is_stripped():
    """只认「中华人民共和国」这一个前缀, 同为「中国」开头的别的名字不动.

    这是**故意**收窄的: 削得越宽, 越容易把两个本来不同的主体归成同一个.
    """
    assert normalize_item_name("中国银行保险监督管理委员会") == (
        "中国银行保险监督管理委员会"
    )


def test_different_laws_stay_different():
    """归一是为了对齐, 不是为了把所有名字压成一个."""
    assert normalize_item_name("中华人民共和国数据安全法") != normalize_item_name(
        "中华人民共和国个人信息保护法"
    )


def test_prefix_only_does_not_swallow_the_whole_name():
    """名字本身就等于前缀时不能削成空串."""
    assert normalize_item_name("中国") == "中国"


# ---------------------------------------------------------------------------
# 内容兜底 (问题里没点名主体时)
# ---------------------------------------------------------------------------


def _hit(score: float, item_name: str | None) -> dict:
    entity = {} if item_name is None else {"item_name": item_name}
    return {"distance": score, "entity": entity}


def test_fallback_accepts_a_confident_hit():
    """实测: 没点名的口语化提问在 chunk 召回头名上能到 0.68~0.73."""
    assert _pick_inferred_item_name([_hit(0.71, "中华人民共和国网络安全法")]) == (
        "中华人民共和国网络安全法"
    )


def test_fallback_rejects_a_weak_hit():
    """分数不够就返回 None —— 宁可回答"不知道", 也别硬塞给一份不相干的文档."""
    assert _pick_inferred_item_name([_hit(0.52, "中华人民共和国网络安全法")]) is None


def test_fallback_handles_empty_and_nameless_hits():
    assert _pick_inferred_item_name([]) is None
    assert _pick_inferred_item_name([_hit(0.9, None)]) is None


def test_fallback_ignores_lower_ranked_hits():
    """只看头名: 第二名再高也不算 —— 兜底是在"挑一份文档", 不是"凑一批候选"."""
    hits = [_hit(0.40, "弱项"), _hit(0.95, "另一个")]
    assert _pick_inferred_item_name(hits) is None
