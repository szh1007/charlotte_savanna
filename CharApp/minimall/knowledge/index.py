"""索引脚本 (L5-a): `python -m CharApp.minimall.knowledge.index`.

把商城里**全部启用**的政策文章拉下来, 切分、向量化, 然后**全量重建** Milvus
collection —— 一次跑完就是"索引 = 数据库里当下的启用文章"这个等式.

为什么是手工跑一次而不是自动触发 (本票定了"纯手工, 最小"): 内容变更的频率是
"管理员改一篇文章", 不是"每来一个请求"; 挂在 Django 的保存动作上要处理异步与
失败重试, 那是另一件事. 手工跑的代价是"改完忘了跑", 所以 Admin 保存时会提示一句.

**幂等**: 同一份输入连着跑两次, collection 的内容一模一样 (drop + create +
insert, 没有增量状态); 改一篇文章重跑, 检索立刻是新正文; 下架一篇重跑, 它被
**物理剔除** (不是软删标记).

**失败不毁索引**: 抓取 / 切分 / 向量化全部完成之后才 drop 旧的 collection ——
中途失败 (商城没起 / 模型不在 / Milvus 连不上) 时, 盘上那份索引原样还在, 检索
不至于因为"跑了一次失败的索引"变成空的. (唯一留下的窗口是 **drop 之后、写入之前**
那一下: 它失败就是一个空 collection. 那种失败要重跑一次本命令; 做不到"回滚到旧
索引" —— 旧数据在 drop 那一刻已经没了, 这是全量重建这个选择的代价.)

跑之前要有: Django (8000, 内部端点) · Milvus · embedding 本地模型 (需要按
`CHARAPP_*` 配好, 见仓库根 `.env.example`); rerank 模型与本脚本无关.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from CharAgent.client import load_root_env
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import (
    KnowledgeConfig,
    MinimallConfigError,
    client_from_env,
    knowledge_config_from_env,
)
from CharApp.minimall.knowledge import milvus
from CharApp.minimall.knowledge.chunking import split_document
from CharApp.minimall.knowledge.documents import build_document
from CharApp.minimall.knowledge.embeddings import Embedder, get_embedder

logger = logging.getLogger("CharApp.minimall.knowledge.index")


@dataclass(frozen=True, slots=True)
class IndexReport:
    """一次索引的结果 (给人看的那几行, 也是用例的断言对象).

    attributes:
        articles: 抓到的启用文章数.
        chunks: 切出来并写进向量库的 chunk 数.
        skipped: 正文空白、被跳过的文章 slug (正常语料里应当是空的).
    """

    articles: int
    chunks: int
    skipped: tuple[str, ...]


async def rebuild(
    client: MinimallClient,
    *,
    config: KnowledgeConfig,
    embedder: Embedder | None = None,
) -> IndexReport:
    """全量重建索引 (抓取 → 组装 → 切分 → 向量化 → drop+create+insert).

    Args:
        client: 商城客户端 (脚本自己建一个; 用例可以传接在假商城上的那个).
        config: 知识库配置.
        embedder: 向量化实现; None = 按配置构建单例 (用例注入替身的地方).

    Returns:
        IndexReport: 文章数 / chunk 数 / 被跳过的 slug.

    Raises:
        ValueError: 商城返回的行缺字段 (契约漂了, 由 `build_document` 抛).
        RuntimeError: embedding 模型本地缺失 (带下载命令), 或 Milvus 写入失败.
            两类都是"这次没成", 上层翻成一行日志 + 退出码 1.
    """
    rows = await client.list_knowledge_articles()
    documents = [build_document(row) for row in rows]

    chunk_rows: list[dict] = []
    skipped: list[str] = []
    for document in documents:
        chunks = split_document(document)
        if not chunks:
            skipped.append(document.slug)
        chunk_rows.extend(chunks)

    # 向量化 (同步模型推理 → 线程池): 放在 drop 之前, 失败时旧索引原样留着
    vectors = {"dense": [], "sparse": []}
    if chunk_rows:
        encoder = embedder or get_embedder(config)
        vectors = await asyncio.to_thread(
            encoder.embed_documents, [chunk["content"] for chunk in chunk_rows]
        )

    # 到这里才动向量库: 全量重建 (drop + create), 然后一次写进去
    await asyncio.to_thread(milvus.ensure_collection, config)
    milvus_rows = [
        {
            **chunk,
            "dense_vector": vectors["dense"][index],
            "sparse_vector": vectors["sparse"][index],
        }
        for index, chunk in enumerate(chunk_rows)
    ]
    await asyncio.to_thread(milvus.insert_chunks, config, milvus_rows)

    return IndexReport(
        articles=len(documents), chunks=len(chunk_rows), skipped=tuple(skipped)
    )


async def _run(config: KnowledgeConfig) -> IndexReport:
    """建客户端 → 重建 → 收尾 (同一个事件循环里关掉连接池)."""
    client = client_from_env()
    try:
        return await rebuild(client, config=config)
    finally:
        await client.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    """脚本入口: 读环境 → 重建索引 → 打一行结果.

    Args:
        argv: 命令行参数 (测试传一个空列表来避免吃掉 pytest 的参数).

    Returns:
        int: 0 = 索引已重建; 1 = 配置错 / 运行期失败 (报一行人话, 不打印 traceback
        —— 与两个服务入口同一条规矩).
    """
    parser = argparse.ArgumentParser(
        prog="python -m CharApp.minimall.knowledge.index",
        description="全量重建政策知识库索引 (Milvus collection: ca_knowledge)",
    )
    # 现在没有可配的参数; parse_args 留着是为了 --help 与将来加开关的地方
    parser.parse_args(argv)

    load_root_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = knowledge_config_from_env()
        report = asyncio.run(_run(config))
    except MinimallConfigError as exc:
        logger.error("索引失败 (配置): %s: %s", type(exc).__name__, exc)
        return 1
    except Exception as exc:
        # 网络 / 模型 / 向量库这些运行期失败: 报一行能看懂的话, 细节留在 %s 里
        logger.error("索引失败: %s: %s", type(exc).__name__, exc)
        return 1

    skipped = f", 跳过空白文章 {list(report.skipped)}" if report.skipped else ""
    logger.info(
        "索引已重建: %d 篇文章 → %d 条 chunk (collection=%s%s)",
        report.articles,
        report.chunks,
        milvus.KNOWLEDGE_COLLECTION,
        skipped,
    )
    return 0


__all__ = ["IndexReport", "main", "rebuild"]


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main(sys.argv[1:]))
