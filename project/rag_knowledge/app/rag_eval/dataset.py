"""
评估样本定义模块.

这个文件只负责维护评估用的数据:
1. 测试知识数据集;
2. 测试问题题库;
3. 题库文件的读写位置.

阅读顺序建议:
1. 先看 `build_import_chunks()`, 理解测试知识有哪些;
2. 再看 `build_web_search_docs()`, 理解联网占位数据;
3. 最后看 `load_batch_eval_cases()`, 理解题库文件如何读取.
"""

import json
from pathlib import Path

# 当前演示样本对应的主体名称与文件标题.
# 与真实知识库(hak180产品安全手册_new.json)中的 item_name / file_title 保持一致.
TEST_ITEM_NAME = "HAK_180烫金机"
TEST_FILE_TITLE = "hak180产品安全手册"

# 评估过程中生成的题库文件和报告统一放到包内 artifacts 目录.
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
GENERATED_BATCH_CASES_FILE = ARTIFACTS_DIR / "eval_cases.json"

# 真实加载产物(chunks json), 作为评测知识数据的来源.
IMPORT_CHUNKS_JSON_FILE = (
    Path(__file__).resolve().parents[2]
    / "output"
    / "hak180产品安全手册"
    / "hak180产品安全手册_new.json"
)


def build_import_chunks() -> list[dict]:
    """
    加载真实知识库测试切片.

    返回值:
    - list[dict]: 可直接送入项目导入链路的 chunk 列表

    来源:
    - `output/hak180产品安全手册/hak180产品安全手册_new.json`(加载图分块产物)

    说明:
    - 列表顺序即 json 数组索引(0~18), 题库中的"索引标注"以此为准;
    - 字段包含 file_title / parent_title / title / part / content / item_name.
    """
    if not IMPORT_CHUNKS_JSON_FILE.is_file():
        raise FileNotFoundError(f"测试知识文件不存在: {IMPORT_CHUNKS_JSON_FILE}")
    return json.loads(IMPORT_CHUNKS_JSON_FILE.read_text(encoding="utf-8"))


def build_web_search_docs() -> list[dict]:
    """
    构造联网占位结果.

    返回值:
    - list[dict]: 模拟联网检索结果的列表

    字段说明:
    - title: 网页标题
    - text: 网页摘要(与 rerank 阶段读取的字段名一致)
    - url: 网页地址

    为什么只放一条固定数据:
    - 当前项目 rerank 阶段要求同时存在本地候选和联网候选;
    - 评测重点是本地召回链路, 所以这里用稳定占位数据避免联网波动干扰结果.
    """
    return [
        {
            "title": "联网占位结果",
            "text": "这是一条联网占位结果, 用于满足 rerank 入参, 不提供关键答案.",
            "url": "https://example.com/hak180-placeholder",
        }
    ]


def load_batch_eval_cases() -> list[dict]:
    """
    读取批量评测题库.

    返回值:
    - list[dict]: 题库列表; 如果文件不存在则返回空列表

    题库文件不存在时, 通常说明还没先执行测试数据入库.
    """
    if not GENERATED_BATCH_CASES_FILE.exists():
        return []
    return json.loads(GENERATED_BATCH_CASES_FILE.read_text(encoding="utf-8"))
