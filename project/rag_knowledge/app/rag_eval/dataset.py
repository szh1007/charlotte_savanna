"""
评估样本定义模块.

这个文件只负责题库文件的位置与读取.

评测知识本身由**真实加载链路** (`load_graph`) 建进 Milvus, 评测包不再自己导入一份
测试数据 —— 早期那套「评测数据入库」把同一篇文档二次灌库, 既和真实索引重复, 又让
题库的 gold_chunk_ids 指向一份只有评测才存在的副本. 现在题库里的 chunk_id 指向的
就是线上检索会命中的那些块.
"""

import json
from pathlib import Path

# 评估过程中生成的题库文件和报告统一放到包内 artifacts 目录.
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
GENERATED_BATCH_CASES_FILE = ARTIFACTS_DIR / "eval_cases.json"


def load_batch_eval_cases() -> list[dict]:
    """
    读取批量评测题库.

    返回值:
    - list[dict]: 题库列表; 如果文件不存在则返回空列表

    题库文件不存在时, 通常说明题库还没准备好.
    """
    if not GENERATED_BATCH_CASES_FILE.exists():
        return []
    return json.loads(GENERATED_BATCH_CASES_FILE.read_text(encoding="utf-8"))
