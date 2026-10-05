"""C02 回归: 分块的两处真 bug.

1) **补 `parent_title` 的时点错了**: 合并的判据是「两块 parent_title 相同」,
    而未切分的块要到 `_padding_chunks_metadata` 才拿到它 —— 那个调用此前排在合并
    **之后**, 于是这类块在判据里恒为 None. 修法是把补齐提到合并之前.

2) **切分器没兜底**: `_split_chunk_content` 传的 separators 少了 LangChain 最后
    那个 `""`(按字符硬切的兜底), 于是一段没有分隔符的文本会**整段返回**,
    chunk_size 形同虚设. 真实产物里出现过 793 / 944 / 1403 字符的块.

末尾一节钉 `_split_document_by_title` 自己的切分契约 (连续标题 / 无标题内容 /
代码块), 它们此前只有经过 section_path 的间接覆盖.
"""

from __future__ import annotations

from app.rag.load import split_service
from app.rag.load.config import CHUNK_MAX_SIZE


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


def test_adjacent_small_chunks_across_titles_are_merged(tmp_path):
    """正文极短的碎片要跨标题被吸收.

    只认「同一个 parent_title」时, 独立小节的 parent_title 各自等于自己的 title,
    相邻碎片永远碰不到一起 —— 实测 131 个 chunk 里 39 个不足 200 字, 最小的
    「## 6.2 菜单项 / 更改设置」只有 15 字, 这种块嵌出的向量几乎没有可检索的语义.
    """
    content = "## 重要事项\n请勿混装。\n## 乙\n" + "乙" * 200 + "\n"
    md_path = tmp_path / "doc.md"
    md_path.write_text(content, encoding="utf-8")

    chunks = split_service.split_document(
        {"md_path": str(md_path), "md_content": content, "file_title": "t"}
    )["chunks"]

    assert len(chunks) == 1, "碎片应当被后一节吸收"
    assert "请勿混装。" in chunks[0]["content"]
    assert "## 乙" in chunks[0]["content"], "跨节合并要保留下一个块的标题"


def test_normal_sized_sections_are_not_merged_across_titles():
    """反例: 正文够长的相邻小节**不能**被跨节合并.

    判据放宽到「同一份文档内相邻即可」时, 一个块里会混进两个主题 ——
    实测 4 份项目文档 106 个块里 32 个含两个以上章节标题, 一个块里既有
    「五、功能与付费差异」又有「六、API 契约」, 对哪个主题都匹配不好.
    """
    chunks = [
        {"file_title": "t", "title": "## 甲", "content": "## 甲\n" + "甲" * 200},
        {"file_title": "t", "title": "## 乙", "content": "## 乙\n" + "乙" * 200},
    ]

    merged = split_service._merge_chunk_content(chunks)

    assert len(merged) == 2, "正常大小的小节不该被并到一起"


def test_chunks_from_different_files_are_never_merged():
    """反例: 跨文档的块不能被并到一起 (合并只限同一份文档的相邻块)."""
    chunks = [
        {"file_title": "doc-a", "title": "## 甲", "content": "甲" * 100},
        {"file_title": "doc-b", "title": "## 乙", "content": "乙" * 100},
    ]

    merged = split_service._merge_chunk_content(chunks)

    assert len(merged) == 2, "不同文档的块不能被并在一起"


def test_merge_stops_before_exceeding_max_size():
    """边界: 碎片 + 超大块, 合并后会超过 CHUNK_MAX_SIZE 就不并入."""
    chunks = [
        {"file_title": "t", "title": "## 甲", "content": "## 甲\n短提示"},
        {"file_title": "t", "title": "## 乙", "content": "乙" * CHUNK_MAX_SIZE},
    ]

    merged = split_service._merge_chunk_content(chunks)

    assert len(merged) == 2, "合并后超过上限就不该并"
    assert merged[0]["content"] == "## 甲\n短提示"


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
    # 上限是 CHUNK_MAX_SIZE 而不是 CHUNK_SIZE: 最后一片过短时会并回前一片,
    # 并完的前一片允许超过目标块大小 (否则小尾巴会独立成块).
    assert all(len(p["content"]) <= CHUNK_MAX_SIZE for p in pieces)


# ---------------------------------------------------------------------------
# 2.5) 表格 / 伪标题 (本轮新增)
# ---------------------------------------------------------------------------


def _table(rows: int) -> str:
    cells = "".join(
        f"<tr><td>第{i}行的单元格内容</td><td>取值{i}</td></tr>" for i in range(rows)
    )
    return f"<table>{cells}</table>"


def test_html_table_is_not_cut_in_the_middle():
    """表格不能被从中间切开.

    MinerU 把整张表压成一行且内部没有分隔符, 递归切分器会按末尾的 "" 兜底规则
    按字符硬切, 切出 "。</td><td>" 这种残片 (实测 18/131 个 chunk 带 HTML 残渣).
    """
    table = _table(15)
    chunk = _chunk("## 菜单项", f"## 菜单项\n{table}")

    pieces = split_service._split_chunk_content(chunk)

    assert len(pieces) == 1, "上限以内的表格应当整块保留, 不被切开"
    assert pieces[0]["content"].count("<table>") == 1
    assert pieces[0]["content"].count("</table>") == 1
    assert table in pieces[0]["content"], "表格本身要原样保留"


def test_oversized_table_splits_on_rows_and_each_piece_is_closed():
    """超过上限的表格按行边界切, 且**每一片自己闭合**.

    直接把 </table> 留在最后一片, 前面几片就成了「有 <td> 没有 <table>」的残片.
    """
    pieces = split_service._split_table_block(_table(120))

    assert len(pieces) > 1, "超长表格应当被切开"
    for piece in pieces:
        assert piece.startswith("<table>"), "每片都要补回开标签"
        assert piece.endswith("</table>"), "每片都要补回闭标签"
        assert piece.count("<table>") == piece.count("</table>") == 1


def test_letter_pseudo_titles_do_not_start_a_new_section(tmp_path):
    """MinerU 误判出来的伪标题 (`## a 按 (Foil Save)...`) 要当内容行处理.

    否则每个操作步骤自成一个 section, 标题退化成 "## a", 丢掉所属章节,
    嵌出来的向量也就没了可检索的语义.
    """
    content = "## 6.4.2 跳过模式\n正文说明\n## a 按 (Foil Save) 按钮。\n继续说明\n"
    md_path = tmp_path / "doc.md"
    md_path.write_text(content, encoding="utf-8")

    chunks = split_service.split_document(
        {"md_path": str(md_path), "md_content": content, "file_title": "t"}
    )["chunks"]

    titles = [c["title"] for c in chunks]
    assert titles == ["## 6.4.2 跳过模式"], f"伪标题不该自成一块: {titles}"
    assert "## a 按 (Foil Save) 按钮。" in chunks[0]["content"], (
        "伪标题行本身要留在正文里"
    )


def test_numeric_titles_are_not_treated_as_pseudo():
    """反例: 「## 10 错误消息」这种数字标题是真标题, 不能被当成伪标题吞掉."""
    content = "## 9 规格\n规格说明\n## 10 错误消息\n错误说明\n"
    md_path_content = content
    chunks = split_service._split_document_by_title(md_path_content, "t")

    titles = [c["title"] for c in chunks]
    assert "## 9 规格" in titles
    assert "## 10 错误消息" in titles


# ---------------------------------------------------------------------------
# 3) 验收: 真实产物里的碎片形态
# ---------------------------------------------------------------------------


def test_every_piece_respects_chunk_size_even_with_long_lines():
    """一行就比 chunk_size 还长时, 同样要被切开 (第二个真实受害形态)."""
    chunk = _chunk("## 标题", "## 标题\n" + "a" * 900 + "\n" + "b" * 900)

    pieces = split_service._split_chunk_content(chunk)

    assert all(len(p["content"]) <= CHUNK_MAX_SIZE for p in pieces)


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
    """长块切出来的碎片要带 parent_title/part —— 合并与溯源都靠它.

    正文给到 1800 字是有意的: 太短的话最后一片会被并回前一片 (见
    `_split_chunk_content` 的尾巴处理), 只剩一块, 这个用例就构造不出多片场景了.
    """
    long_section = "## 长节\n" + ("句子。" * 600)
    md_path = tmp_path / "doc.md"
    md_path.write_text(long_section, encoding="utf-8")

    chunks = split_service.split_document(
        {"md_path": str(md_path), "md_content": long_section, "file_title": "t"}
    )["chunks"]

    assert len(chunks) > 1
    assert {c["parent_title"] for c in chunks} == {"## 长节"}
    assert [c["part"] for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(len(c["content"]) <= CHUNK_MAX_SIZE for c in chunks)


# ---------------------------------------------------------------------------
# 3) 祖先章节路径: 检索正文的语义锚
#
# 背景: content 首行原本只是块自己的标题 (`### 5.2 环境变量`), 不含所属父节,
# 于是问「快速开始包含哪些步骤」时这一块整段没有「快速开始」四个字, 召回和
# 精排都认不出它 —— 实测它在全文档 24 条里排第 23 名.
# ---------------------------------------------------------------------------


def _split_md(tmp_path, content: str) -> list[dict]:
    """跑一遍完整切分链路 (含路径拼接), 返回最终 chunks."""
    md_path = tmp_path / "doc.md"
    md_path.write_text(content, encoding="utf-8")
    return split_service.split_document(
        {"md_path": str(md_path), "md_content": content, "file_title": "t"}
    )["chunks"]


def test_content_head_carries_the_full_ancestor_chain(tmp_path):
    """子节的 content 首行要带完整祖先链, 而不是只有它自己那一级."""
    chunks = _split_md(
        tmp_path,
        "# 项目说明\n\n## 5. 快速开始\n\n### 5.2 环境变量\n密钥填在 .env 里。\n",
    )

    target = next(c for c in chunks if "5.2" in c["title"])
    assert target["content"].splitlines()[0] == "## 5. 快速开始 > ### 5.2 环境变量"


def test_document_h1_stays_out_of_the_path(tmp_path):
    """文档 H1 不进路径 —— 它已由 item_name 过滤表达, 进了只会让全库的块更像."""
    chunks = _split_md(tmp_path, "# 项目说明\n\n## 一、简介\n正文内容。\n")

    target = next(c for c in chunks if "简介" in c["title"])
    assert not target["content"].startswith("# 项目说明")
    assert target["content"].splitlines()[0] == "## 一、简介"


def test_a_new_sibling_section_pops_the_previous_branch(tmp_path):
    """开到下一个大节时上一个分支要出栈 —— 否则路径会跨节越挂越长.

    正文给到 100 字是有意的: 短于 CROSS_MERGE_BODY_MIN 的块会被当成碎片并被
    下一节吸收 (见 `_merge_chunk_content`), 那样就只剩一块, 构造不出「两个大节」
    的场景.
    """
    chunks = _split_md(
        tmp_path,
        "## 5. 快速开始\n\n### 5.2 环境变量\n"
        + "甲" * 100
        + "\n\n## 6. 常见问题\n"
        + "乙" * 100
        + "\n",
    )

    last = next(c for c in chunks if "6." in c["title"])
    assert last["content"].splitlines()[0] == "## 6. 常见问题"


def test_content_outside_any_section_is_left_untouched(tmp_path):
    """文档开头那段 (H1 + 简介) 没有章节路径, 内容不该被动."""
    chunks = _split_md(tmp_path, "# 项目说明\n\n这是项目简介, 不属于任何小节。\n")

    assert chunks[0]["content"].startswith("# 项目说明")


def test_split_pieces_keep_the_ancestor_chain(tmp_path):
    """长节切出来的每一片都带同一条祖先链 —— 归属不随切分改变."""
    body = "## 5. 快速开始\n\n### 5.1 前置服务\n" + ("句子。" * 600)
    chunks = _split_md(tmp_path, body)

    assert len(chunks) > 1
    for piece in chunks:
        assert piece["content"].splitlines()[0] == "## 5. 快速开始 > ### 5.1 前置服务"


# ---------------------------------------------------------------------------
# 4) 多级标题切分契约: 连续标题 / 无标题内容 / 代码块
#
# 入口是 `_split_document_by_title` —— 上面那些用例大多走完整 pipeline, 只能
# 间接覆盖它; 这一节直接打这三个契约, 免得哪条被改坏时还被下游的分支掩盖.
# ---------------------------------------------------------------------------


def test_consecutive_titles_are_joined_into_a_parent_child_chain():
    """连续出现的多级标题要拼成一条链, 而**不是**丢掉子标题只留第一个.

    `## 5. 快速开始` 紧跟 `### 5.2 环境变量` 且中间没有正文时, 子节的归属
    信息只存在于这个拼接结果里 —— 拼错了, 块就同时丢了「5.2 环境变量」这一级.
    """
    chunks = split_service._split_document_by_title(
        "## 5. 快速开始\n### 5.2 环境变量\n密钥填在 .env 里。\n", "t"
    )

    assert len(chunks) == 1, "连续标题之间没有正文, 应当合成一块"
    assert chunks[0]["title"] == "## 5. 快速开始_### 5.2 环境变量"
    assert chunks[0]["section_path"] == "## 5. 快速开始 > ### 5.2 环境变量"


def test_content_before_the_first_title_belongs_to_the_next_section():
    """文档开头那段没有标题的内容要归给紧接着的下一个标题, 而不是被丢掉."""
    chunks = split_service._split_document_by_title(
        "这是一段没有标题的开头说明\n\n## 甲\n正文甲\n", "t"
    )

    assert len(chunks) == 1
    assert chunks[0]["title"] == "## 甲"
    assert chunks[0]["content"] == "## 甲\n这是一段没有标题的开头说明\n正文甲"


def test_hashes_inside_a_code_fence_do_not_start_a_section():
    """代码块里的 `# 注释` 不是标题, 整个代码块保持在一块里.

    若把围栏里的 `#` 行当成真标题, 一个代码块会被切成好几节: 每行注释各成
    标题, 代码上下文被拆散.
    """
    chunks = split_service._split_document_by_title(
        "## 甲\n```python\n# 不是标题\nprint(1)\n```\n正文甲\n", "t"
    )

    assert len(chunks) == 1
    assert chunks[0]["title"] == "## 甲"
    assert "```python\n# 不是标题\nprint(1)\n```" in chunks[0]["content"]


def test_section_path_is_snapshotted_when_each_chunk_is_settled():
    """section_path 是结算每一块那一刻的栈快照: 三级都进链, 新兄弟节点要出栈.

    只在最终正文里看路径的话, 中间态的错法 (比如 5.2 仍带着 5.1.1 的祖先)
    要等 `_attach_section_path` 换首行时才暴露; 直接看字段钉的是「栈在结算
    时是什么样」.
    """
    md = (
        "## 5. 快速开始\n正文0\n\n"
        "### 5.1 前置服务\n正文1\n\n"
        "#### 5.1.1 启动 Milvus\n正文2\n\n"
        "### 5.2 环境变量\n正文3\n"
    )

    chunks = split_service._split_document_by_title(md, "t")
    paths = {c["title"]: c["section_path"] for c in chunks}

    assert paths["## 5. 快速开始"] == "## 5. 快速开始"
    assert paths["### 5.1 前置服务"] == "## 5. 快速开始 > ### 5.1 前置服务"
    assert paths["#### 5.1.1 启动 Milvus"] == (
        "## 5. 快速开始 > ### 5.1 前置服务 > #### 5.1.1 启动 Milvus"
    )
    assert paths["### 5.2 环境变量"] == "## 5. 快速开始 > ### 5.2 环境变量"
