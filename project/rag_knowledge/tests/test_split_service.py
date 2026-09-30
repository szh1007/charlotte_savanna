"""C02 回归: 分块的两处真 bug.

1) **补 `parent_title` 的时点错了**: 合并的判据是「两块 parent_title 相同」,
    而未切分的块要到 `_padding_chunks_metadata` 才拿到它 —— 那个调用此前排在合并
    **之后**, 于是这类块在判据里恒为 None. 修法是把补齐提到合并之前.

2) **切分器没兜底**: `_split_chunk_content` 传的 separators 少了 LangChain 最后
    那个 `""`(按字符硬切的兜底), 于是一段没有分隔符的文本会**整段返回**,
    chunk_size 形同虚设. 真实产物里出现过 793 / 944 / 1403 字符的块.
"""

from __future__ import annotations

from app.rag.load import split_service
from app.rag.load.config import CHUNK_MAX_SIZE, CHUNK_SIZE


def _chunk(title: str, content: str) -> dict:
    return {"file_title": "t", "title": title, "content": content}


# ---------------------------------------------------------------------------
# 1) 补齐时点
# ---------------------------------------------------------------------------


def test_unsplit_chunks_get_parent_title_before_merging(monkeypatch, tmp_path):
    """合并前每一块都得有 parent_title —— 否则判据恒为假, 合并永远不触发."""
    seen: list[bool] = []
    original = split_service._merge_chunk_content

    def spy(chunks):
        seen.extend("parent_title" in chunk for chunk in chunks)
        return original(chunks)

    # 用 monkeypatch 而不是手工赋值 + try/finally: 它自动还原, 用例失败时也不会漏掉
    monkeypatch.setattr(split_service, "_merge_chunk_content", spy)

    split_service.split_document(
        {
            "md_path": str(tmp_path / "doc.md"),
            "md_content": "## 甲\n短内容\n## 乙\n另一段短内容\n",
            "file_title": "t",
        }
    )

    assert seen, "合并函数应当被调用到"
    assert all(seen), "进合并之前每一块都该已经有 parent_title"


def test_adjacent_small_chunks_under_the_same_title_are_merged(tmp_path):
    """同一标题下相邻的小块真的会被并起来 (这是「合并生效」的最小实例)."""
    content = "## 设备\n" + "甲" * 100 + "\n## 设备\n" + "乙" * 100 + "\n"
    md_path = tmp_path / "doc.md"
    md_path.write_text(content, encoding="utf-8")

    chunks = split_service.split_document(
        {"md_path": str(md_path), "md_content": content, "file_title": "t"}
    )["chunks"]

    same_title = [c for c in chunks if c["title"] == "## 设备"]
    assert len(same_title) == 1, "同标题的两个短板应当被并成一块"
    assert "甲" in same_title[0]["content"] and "乙" in same_title[0]["content"]


def test_chunks_with_different_titles_are_not_merged(tmp_path):
    """反例: 标题不同的相邻短板**不该**被并 —— 补 parent_title 不等于乱并."""
    content = "## 甲\n" + "甲" * 100 + "\n## 乙\n" + "乙" * 100 + "\n"
    md_path = tmp_path / "doc.md"
    md_path.write_text(content, encoding="utf-8")

    chunks = split_service.split_document(
        {"md_path": str(md_path), "md_content": content, "file_title": "t"}
    )["chunks"]

    titles = [c["title"] for c in chunks]
    assert "## 甲" in titles and "## 乙" in titles, "不同标题的块不能被并到一起"


# ---------------------------------------------------------------------------
# 2) 切分器的兜底
# ---------------------------------------------------------------------------


def test_a_text_without_any_separator_is_still_hard_split():
    """回归本体: 没有分隔符的长文本必须被按字符切开.

    修复前这段会整段返回 (实测 chunk_size=574 切 'x'*1400 得到 1 片 1400 字符),
    典型受害者是被压成一行的 HTML 表格.
    """
    long_blob = "x" * 1400
    chunk = _chunk("## 标题", "## 标题\n" + long_blob)

    pieces = split_service._split_chunk_content(chunk)

    assert len(pieces) > 1, "没有分隔符的长文本也必须被切开"
    assert all(len(p["content"]) <= CHUNK_SIZE for p in pieces)


def test_every_piece_respects_chunk_size_even_with_long_lines():
    """一行就比 chunk_size 还长时, 同样要被切开 (第二个真实受害形态)."""
    chunk = _chunk("## 标题", "## 标题\n" + "a" * 900 + "\n" + "b" * 900)

    pieces = split_service._split_chunk_content(chunk)

    assert all(len(p["content"]) <= CHUNK_SIZE for p in pieces)


def test_a_normal_short_section_is_left_alone(tmp_path):
    """反例: 本来就不长的节不该被切 —— 免得兜底把正常内容切碎."""
    content = "## 标题\n短内容\n"
    md_path = tmp_path / "doc.md"
    md_path.write_text(content, encoding="utf-8")

    chunks = split_service.split_document(
        {"md_path": str(md_path), "md_content": content, "file_title": "t"}
    )["chunks"]

    assert len(chunks) == 1
    assert chunks[0]["content"] == "## 标题\n短内容"


def test_pieces_of_one_long_title_keep_their_parent(tmp_path):
    """长块切出来的碎片要带 parent_title/part —— 合并与溯源都靠它."""
    long_section = "## 长节\n" + ("句子。" * 300)
    md_path = tmp_path / "doc.md"
    md_path.write_text(long_section, encoding="utf-8")

    chunks = split_service.split_document(
        {"md_path": str(md_path), "md_content": long_section, "file_title": "t"}
    )["chunks"]

    assert len(chunks) > 1
    assert {c["parent_title"] for c in chunks} == {"## 长节"}
    assert [c["part"] for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(len(c["content"]) <= CHUNK_MAX_SIZE for c in chunks)
