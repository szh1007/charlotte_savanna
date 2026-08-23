"""输入文档材料检索源 (Issue 07).

统一管道 (ADR-0002) 中材料输入本身也是检索源: 检索增强阶段可在
联网之外回查用户材料原文. 简单实现: 按段落切分 + 查询词命中排序
(材料通常数百行, 无需向量检索).
"""

import logging
import re

from .base import SOURCE_DOCUMENT, SearchResult

logger = logging.getLogger(__name__)

_MAX_PARAGRAPH_CHARS = 2000

# 查询分词字符类: 中英混排分隔 (全角标点为刻意支持, RUF001)
_QUERY_SPLIT_RE = re.compile(r"[\s,，。、]+")  # noqa: RUF001


class DocumentSource:
    """输入材料检索源: 关键词命中的段落优先, 无命中返回开头段落."""

    name = SOURCE_DOCUMENT
    description = (
        "检索用户本次输入的学习材料原文: 查材料中的定义/示例/章节内容, 输入想找的主题词"
    )

    def __init__(self, material_text: str):
        self._paragraphs = self._split_paragraphs(material_text)

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        # 空行分隔段落; 无空行时整体作为一段 (材料量小, 检索粒度可用)
        return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()] or [
            text.strip()
        ]

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        terms = [t for t in _QUERY_SPLIT_RE.split(query.lower()) if len(t) > 1]
        scored: list[tuple[int, int]] = []  # (段落索引, 命中词数)
        for idx, para in enumerate(self._paragraphs):
            low = para.lower()
            hits = sum(1 for t in terms if t in low)
            if hits:
                scored.append((idx, hits))
        # 命中段按 (命中词数多优先, 位置靠前优先) 排序
        scored.sort(key=lambda x: (-x[1], x[0]))
        selected = scored[:max_results]
        if not selected:
            # 无命中回退: 返回材料开头段落 (保证检索增强有材料可引用)
            selected = [(i, 0) for i in range(min(max_results, len(self._paragraphs)))]
        results = []
        for idx, _ in selected:
            results.append(
                SearchResult(
                    title=f"材料原文 (第 {idx + 1} 段)",
                    url="",
                    content=self._paragraphs[idx][:_MAX_PARAGRAPH_CHARS],
                    source_type=SOURCE_DOCUMENT,
                    metadata={"paragraph": idx},
                )
            )
        return results
