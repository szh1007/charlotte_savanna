"""DB 行 → 文档 (L5-a): 语料从「一条记录」变成「一份能切分的文档」.

一句话理解: 商城端点给回来的是五个字段的 JSON, 而切分器要的是一篇有结构的文本 ——
这里就是那一步翻译, 也是**唯一**知道「一篇文档有哪些来源字段」的地方.

组装规则 (为什么这么定):

- **正文第一行是 `# {title}`**: 标题进正文是有意的 —— 切分器按 `\\n## ` 优先切,
  第一块因此天然带着标题, 一个只问「退款政策是什么」的查询能靠它命中; 而文章正文
  自己**不再写**一级标题 (语料约定, 见 `app/minimall/knowledge_corpus.py`), 否则
  检索结果里会出现两个标题, 看着像 bug.
- **`category` 不进正文**, 只随 metadata 进 Milvus (schema 里那一列): 它是给后续
  过滤留的机器键 (code, 如 `shipping`); 写进正文对模型是噪音, 而换成中文标签就得
  把 Django 那份 TextChoices 复制到这里 —— 多一份会漂的东西, 换不来检索质量.
- **`updated_at` 也不进来**: 索引脚本每次都全量重建, 向量库里没有"新旧"可言;
  真要看一篇文章什么时候改的, Admin 里看得到.

契约漂了要让脚本**当场报错**: 少一个键就抛 `ValueError` (带上缺的是哪个、收到的是
什么) —— 静默拼出一篇没有标题的文档, 症状会是"检索得到但答不对", 离真因很远.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# 一篇文档必须有的四个字段 (与 `AgentKnowledgeArticleSerializer` 的契约对应)
REQUIRED_FIELDS = ("slug", "title", "category", "content")


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """一篇知识文章 (索引链路的输入).

    attributes:
        slug: 稳定标识; chunk 主键由它拼成 (`{slug}-{序号}`).
        title: 标题 (组装正文时拼在最前面).
        category: 分类 code (`policy` / `shipping` / …), 只进 metadata.
        content: Markdown 正文 (约定不带一级标题).
    """

    slug: str
    title: str
    category: str
    content: str


def build_document(row: Mapping[str, Any]) -> KnowledgeDocument:
    """商城端点的返回行 → 一篇文档.

    Args:
        row: `GET knowledge/articles/` 返回体里的一条 (至少含 `REQUIRED_FIELDS`).

    Returns:
        KnowledgeDocument: 四个字段都已去首尾空白.

    Raises:
        ValueError: 缺字段. 读的是**商城的契约**, 缺了说明两边对不上了 —— 当场
            说清比拼出一篇残缺的文档强.
    """
    missing = [name for name in REQUIRED_FIELDS if name not in row]
    if missing:
        raise ValueError(
            f"知识文章缺字段 {missing}: 商城端点与索引脚本的契约对不上了 "
            f"(收到的是 {sorted(row)})"
        )
    return KnowledgeDocument(
        slug=str(row["slug"]).strip(),
        title=str(row["title"]).strip(),
        category=str(row["category"]).strip(),
        content=str(row["content"]).strip(),
    )


def as_text(document: KnowledgeDocument) -> str:
    """文档 → 喂给切分器的整篇文本 (`# 标题` + 空行 + 正文).

    正文为空时只留下标题行 —— 切分器那边对此有兜底 (整篇空白会被跳过并在报告里
    列名), 这里不替它做决定.
    """
    parts = [f"# {document.title}"]
    if document.content:
        parts.append(document.content)
    return "\n\n".join(parts)


__all__ = ["REQUIRED_FIELDS", "KnowledgeDocument", "as_text", "build_document"]
