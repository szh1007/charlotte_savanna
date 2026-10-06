"""把评测语料灌进 Milvus (C17).

跑评测前必须先跑这个 —— 题库的 gold_chunk_ids 指向 Milvus 的**自增主键**,
索引重建后 id 全变, 题库得跟着重标.

```bash
python scripts/load_eval_corpus.py              # 全量 (15 篇)
python scripts/load_eval_corpus.py --only 数据出境安全评估办法   # 只灌某一篇
```

**为什么先复制到临时目录再加载**: 加载链路会把 chunks 备份 json 写在 md 文件**旁边**
(`split_service._backup_chunks_json`), 直接从 `assets/eval_corpus/` 加载会在语料目录里
撒下一堆派生的 `.json` —— 它们是可再生的中间产物, 里面的 chunk_id 每次重建都变,
提交进仓库只会让人误把它当成语料的一部分. 副本放临时目录, 备份跟着落在那里,
正好拿来给题库标 gold.

同一个文件重复加载是**幂等**的: 入库前按 `file_title` 删旧插新
(`index_service._insert_chunks_data`), 不会产生副本.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from app.process.load.agent.main_graph import graph
from app.process.load.agent.state import create_default_state

CORPUS_DIR = Path(__file__).resolve().parents[1] / "assets/eval_corpus"
# 语料目录里还放着一份出处说明, 它不是语料 —— 灌进去会凭空多出一个"主体"
NOT_CORPUS = {"SOURCES.md"}


def load_one(path: Path, task_id: str) -> dict:
    """灌一篇, 返回 `{item_name, chunks, file_title}`."""
    state = create_default_state(
        task_id=task_id,
        local_file_path=str(path),
        is_md_read_enabled=True,
        is_pdf_read_enabled=False,
    )
    result = graph.invoke(state)
    return {
        "file_title": result.get("file_title", ""),
        "item_name": result.get("item_name", ""),
        "chunk_count": len(result.get("chunks") or []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="把评测语料灌进 Milvus (C17)")
    parser.add_argument("--only", default=None, help="只灌文件名含这段文字的文档")
    args = parser.parse_args()

    sources = sorted(p for p in CORPUS_DIR.glob("*.md") if p.name not in NOT_CORPUS)
    if args.only:
        sources = [p for p in sources if args.only in p.stem]
    if not sources:
        raise SystemExit(f"没有匹配的语料: {CORPUS_DIR} (--only={args.only})")

    work = Path(tempfile.gettempdir()) / "rag_eval_corpus_load"
    work.mkdir(parents=True, exist_ok=True)

    total = 0
    for i, src in enumerate(sources, start=1):
        staged = work / src.name
        shutil.copy2(src, staged)
        print(f"[{i}/{len(sources)}] 灌库: {src.stem}")
        info = load_one(staged, task_id=f"c17_load_{src.stem}")
        total += info["chunk_count"]
        print(f"    主体={info['item_name']!r}  chunk={info['chunk_count']}")

    print(f"\n完成: {len(sources)} 篇, 共 {total} 个 chunk")
    print(f"chunks 备份 (标 gold 用): {work}")


if __name__ == "__main__":
    main()
