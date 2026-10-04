"""文档切分 (L5-a): 按**来源类型**查参数表 —— 当前只有 `article` 一档.

搬运自 `project/charplot/rag/chunking.py`, 只换了一个键: 那边按**文件扩展名**查表
(它吃的是上传文件, 有 PDF / docx / html), 这里的语料全部来自数据库里的 Markdown
短文, 没有扩展名, 但有来源类型 —— 表键改成 `source_type`, 将来加「会话记录」
「商品说明」这类新来源时各自一行, 不用碰切分逻辑.

切分器仍是 LangChain 的 `RecursiveCharacterTextSplitter`: 按自然分隔符递归, 优先
在 Markdown 小节标题处断开, 超长段落才退到句号 / 空格. 参数 500/50 与 charplot 的
md 档一致 —— 政策短文一段通常几十到一百来字, 500 字一块能装下两三段, 语义完整.

**chunk 主键是显式的** `f"{slug}-{chunk_index}"` (charplot 同款): 检索结果据此
反解来源, C10 的逐句引用也拿它做稳定标识. 不用 `auto_id` —— 那样子外部对不上
chunk 编号, 只能回查 (`rag_knowledge` 的 `auto_id=True` 就是这个代价).
"""

from __future__ import annotations

import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter

from CharApp.minimall.knowledge.documents import KnowledgeDocument, as_text

logger = logging.getLogger(__name__)

# 来源类型: 数据库里的政策文章 (唯一的来源, 常量留在这里给调用方引用)
SOURCE_TYPE_ARTICLE = "article"

# 来源类型 → (chunk_size, chunk_overlap). 未列出的类型回退到 article 档并在
# debug 里记一句 —— 新来源上线时会先在这儿看见它, 而不是悄悄用了别人的参数.
_CHUNK_PARAMS: dict[str, tuple[int, int]] = {
    SOURCE_TYPE_ARTICLE: (500, 50),
}

# 按来源类型的分隔符偏好: article 是 Markdown, 优先按小节标题切, 代码块不硬切
_SEPARATORS: dict[str, list[str]] = {
    SOURCE_TYPE_ARTICLE: [
        "\n## ",
        "\n### ",
        "\n#### ",
        "\n```",
        "\n\n",
        "\n",
        ". ",
        " ",
    ],
}


def _build_splitter(source_type: str) -> RecursiveCharacterTextSplitter:
    """按来源类型构建切分器 (参数 + 分隔符集)."""
    if source_type not in _CHUNK_PARAMS:
        # 新来源上线时会先在这儿看见它, 而不是悄悄用了别人的参数
        logger.debug("无 %s 类型的切分配置, 回退 article 档", source_type)
    size, overlap = _CHUNK_PARAMS.get(source_type, _CHUNK_PARAMS[SOURCE_TYPE_ARTICLE])
    separators = _SEPARATORS.get(source_type, _SEPARATORS[SOURCE_TYPE_ARTICLE])
    return RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=separators,
        strip_whitespace=True,
    )


def split_document(
    document: KnowledgeDocument, *, source_type: str = SOURCE_TYPE_ARTICLE
) -> list[dict]:
    """把一篇文档切成 chunk 行 (**不含向量**, 向量由索引脚本另一步填).

    每行携带 metadata: `id` (显式主键, `{slug}-{序号}`) / `slug` / `title` /
    `category` / `chunk_index` / `content`. 前三项进 Milvus 的对应列, 用于过滤与
    引用; `content` 是切出来的片段正文.

    Args:
        document: 一篇文档 (组装文本由 `documents.as_text` 负责).
        source_type: 来源类型 (决定切分参数); 默认 `article`.

    Returns:
        list[dict]: chunk 行; **正文空白时是空列表** (警告日志里带 slug) ——
        判的是 `content` 而不是组装后的整篇: 标题那一行永远在, 拿它判就永远
        判不出"空", 而一篇只有标题的文档进索引只会给检索添一块噪音.
    """
    if not document.content.strip():
        logger.warning("文章正文为空, 跳过切分 (slug=%s)", document.slug)
        return []
    text = as_text(document).strip()
    splitter = _build_splitter(source_type)
    pieces = splitter.split_text(text)
    chunks = [
        {
            # 显式主键: 谁都能从它看出这篇片段属于哪篇文章的第几块
            "id": f"{document.slug}-{index}",
            "slug": document.slug,
            "title": document.title,
            "category": document.category,
            "chunk_index": index,
            "content": piece,
        }
        for index, piece in enumerate(pieces)
        if piece.strip()
    ]
    logger.info("文档切分完成 (slug=%s): %d chunks", document.slug, len(chunks))
    return chunks


__all__ = ["SOURCE_TYPE_ARTICLE", "split_document"]
