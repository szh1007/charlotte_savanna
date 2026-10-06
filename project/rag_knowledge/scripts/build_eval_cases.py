"""把题库源 (`cases_src.json` 里的文本片段) 解析成评测用的 chunk_id (C17).

```bash
python scripts/build_eval_cases.py
```

**为什么要有这一步**: chunk_id 是 Milvus 的自增主键 —— 切分逻辑一改 (调 chunk_size,
改标题切法, 重新灌库) 全部重排. 题库要是直接写死 id, 每次动索引都得人工重标整本,
而且**标错了不报错**, 只是数字悄悄变差. 源文件存可读片段, id 现算, 就绕开了这件事.

**hint 必须全局唯一**: 命中 0 个 = 片段写错了或这段内容被切没了; 命中多个 = 片段不够
特征. 两种都当场报错, 不猜.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.infra.milvus import infra_milvus
from app.shared.config.milvus_config import milvus_config

PKG_DIR = Path(__file__).resolve().parents[1] / "app/rag_eval"
# 题库源与解析产物都放在 artifacts/ 下 —— 它们是一对, 分开放容易只改一个
SRC_FILE = PKG_DIR / "artifacts/cases_src.json"
OUT_FILE = PKG_DIR / "artifacts/eval_cases.json"


def load_chunks() -> list[dict]:
    """把库里所有 chunk 拉回来 —— 解析 hint 只需要 chunk_id 与正文."""
    client = infra_milvus.require_client()
    return client.query(
        collection_name=milvus_config.chunks_collection,
        filter="chunk_id >= 0",
        output_fields=["chunk_id", "file_title", "content"],
        limit=1000,
        consistency_level="Strong",
    )


# 允许一个 hint 命中多个 chunk 的上限. 为什么会多命中: 相邻块之间有 50 字的
# overlap (`CHUNK_OVERLAP`), 落在重叠区的句子会同时出现在两块里 —— 而这**两块都
# 算正确检索** (答案确实在两块里), 所以收进 gold 是对的. 超过这个数就说明片段
# 太泛 (比如撞上一句套话), 那才是真错.
MAX_HITS_PER_HINT = 3


def resolve(hint: str, chunks: list[dict], case_id: str) -> list[str]:
    """文本片段 -> chunk_id 列表.

    命中 0 个 = 片段写错了或那段内容被切没了; 命中跨了**多个文档** = 片段没特征;
    命中数超过 `MAX_HITS_PER_HINT` = 片段太泛. 三种都当场报错, 不猜.
    """
    hits = [r for r in chunks if hint in r["content"]]
    files = sorted({r["file_title"] for r in hits})
    reason = None
    if not hits:
        reason = "没有命中任何 chunk"
    elif len(files) > 1:
        reason = f"跨了 {len(files)} 个文档: {files}"
    elif len(hits) > MAX_HITS_PER_HINT:
        reason = f"命中 {len(hits)} 个 chunk, 片段太泛"
    if reason:
        raise SystemExit(
            f"[{case_id}] hint 无法定位: {reason}\n"
            f"    片段: {hint[:60]}...\n"
            f"    提示: 换一段更有特征的文字, 或先跑 scripts/load_eval_corpus.py"
        )
    return [str(r["chunk_id"]) for r in hits]


def main() -> None:
    src = json.loads(SRC_FILE.read_text(encoding="utf-8"))
    chunks = load_chunks()
    if not chunks:
        raise SystemExit("知识库是空的 —— 先跑 scripts/load_eval_corpus.py")

    built = []
    for case in src["cases"]:
        # 保序去重: 两个 hint 可能落到同一块 (重叠区), gold 里不该出现重复 id
        gold = list(
            dict.fromkeys(
                cid
                for h in case["gold_hints"]
                for cid in resolve(h, chunks, case["case_id"])
            )
        )
        must = list(
            dict.fromkeys(
                cid
                for h in case["must_hit_hints"]
                for cid in resolve(h, chunks, case["case_id"])
            )
        )
        built.append(
            {
                "case_id": case["case_id"],
                "question": case["question"],
                "expected_item_names": case["expected_item_names"],
                "gold_chunk_ids": gold,
                "must_hit_chunk_ids": must,
                # 下面两项评测链路不读, 只随报告一起落盘 —— 难度分布与题型分布
                # 是要写进 README 的, 得有个可查的来源
                "kind": case["kind"],
                "level": case["level"],
            }
        )

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(
        json.dumps(built, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"题库已生成: {OUT_FILE} ({len(built)} 题)")
    print("  题型:", dict(Counter(c["kind"] for c in built)))
    print("  难度:", dict(Counter(c["level"] for c in built)))
    covered = {c["expected_item_names"][0] for c in built if c["expected_item_names"]}
    print(f"  覆盖文档: {len(covered)} 篇")


if __name__ == "__main__":
    main()
