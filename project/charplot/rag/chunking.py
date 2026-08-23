"""文档切分 (Issue 10, SPEC §7.2) - chunk_size/overlap 按文档类型调优.

输入: 解析后的纯文本 (pipeline/parsers.py 产出) + 来源元数据; 输出:
chunk 列表 [{doc_id, title, filename, chunk_index, content, valid}],
metadata 保留来源/文档 id/有效标记 (检索 filter 与来源引用用).

切分器用 langchain RecursiveCharacterTextSplitter (按自然分隔符递归,
代码/表格块不硬切); 不同格式用不同分隔符集与参数:
- md/txt: 500/50 (默认档, 普通文档)
- html: 800/80 (网页正文段落长, 块可稍大)
- pdf/docx/pptx: 600/60 (文档类, 折中档)
"""

import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..api import config

logger = logging.getLogger(__name__)

# 文档类型 → 切分参数 (扩展名小写去点; 未列出的用默认档)
_CHUNK_PARAMS: dict[str, tuple[int, int]] = {
    "md": (500, 50),
    "txt": (500, 50),
    "html": (800, 80),
    "pdf": (600, 60),
    "docx": (600, 60),
    "pptx": (600, 60),
}

# 按扩展名的分隔符偏好: markdown 优先按标题结构切, 代码友好
_SEPARATORS = {
    "md": ["\n## ", "\n### ", "\n#### ", "\n```", "\n\n", "\n", ". ", " "],
    "html": ["\n\n", "\n", "。", ". ", " "],
}


def _chunk_params(extension: str) -> tuple[int, int]:
    """按扩展名取切分参数 (未配置的类型回退默认档)."""
    params = _CHUNK_PARAMS.get(extension.lower())
    if params is None:
        logger.debug("无 %s 类型切分配置, 使用默认档", extension)
        return config.CHUNK_SIZE, config.CHUNK_OVERLAP
    return params


def _build_splitter(extension: str) -> RecursiveCharacterTextSplitter:
    """按扩展名构建切分器 (分隔符集 + 参数)."""
    size, overlap = _chunk_params(extension)
    separators = _SEPARATORS.get(extension.lower())
    return RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=separators,
        strip_whitespace=True,
    )


def split_document(
    text: str,
    *,
    doc_id: int,
    title: str,
    filename: str,
    extension: str,
) -> list[dict]:
    """把单篇文档的纯文本切成 chunk 列表.

    每 chunk 携带 metadata: doc_id/title/filename (来源引用),
    chunk_index (文档内序号), valid=True (有效标记, 软删 filter 兜底;
    Issue 10 重建物理剔除即 valid 恒 True).
    """
    text = text.strip()
    if not text:
        logger.warning("文档内容为空, 跳过切分 (doc_id=%s, %s)", doc_id, filename)
        return []
    splitter = _build_splitter(extension)
    pieces = splitter.split_text(text)
    chunks = [
        {
            "doc_id": doc_id,
            "title": title,
            "filename": filename,
            "chunk_index": idx,
            "content": piece,
            "valid": True,
        }
        for idx, piece in enumerate(pieces)
        if piece.strip()
    ]
    logger.info("文档切分完成 (doc_id=%s): %d chunks", doc_id, len(chunks))
    return chunks
