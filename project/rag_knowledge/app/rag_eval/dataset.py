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
# 逐题冻结的 HyDE 假设答案 —— 和题库一样是**评测的输入**, 必须随题库一起提交,
# 否则重跑时的输入和当次不一致, 两组数字不可比 (详见 runner.run_query_eval_case).
HYDE_ANSWERS_FILE = ARTIFACTS_DIR / "hyde_answers.json"


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


def load_hyde_answers() -> dict[str, dict[str, str]]:
    """
    读取逐题冻结的 HyDE 假设答案.

    返回值:
    - dict[str, dict]: `case_id -> {"题目": 原问题, "假设答案": 那次生成的结果}`;
      文件不存在时返回空字典

    存**原问题**是为了能校验: 冻结的是「针对某个问题生成的假设答案」, 问题一改,
    那份答案就答非所问了 —— 而且不会报错, 只是检索文本悄悄偏掉, 数字变差还查不出
    原因. 取答案一律走 `get_frozen_answer`, 它会拿当前问题比一下.

    空字典不代表"这道题没有 HyDE", 只代表还没抓过 —— 评测链路遇到题库里有、
    冻结文件里没有 (或对不上) 的题会直接报错, 不会静默现场生成.
    """
    if not HYDE_ANSWERS_FILE.exists():
        return {}
    return json.loads(HYDE_ANSWERS_FILE.read_text(encoding="utf-8"))


def get_frozen_answer(
    answers: dict[str, dict[str, str]], case_id: str, question: str
) -> str | None:
    """
    取这道题的冻结假设答案; 题目对不上就返回 None (由调用方重新生成).

    参数:
    - answers: `load_hyde_answers()` 的返回值
    - case_id: 题目 ID
    - question: **当前**题库里的问题原文

    返回值:
    - str | None: 冻结的假设答案; 没抓过或题目已改则 None
    """
    entry = answers.get(case_id)
    # 不是 dict 的一律当"对不上"处理: 早期版本存的是裸字符串 (只有答案没有题目),
    # 无从校验 —— 与其猜, 不如让它重新抓一次
    if not isinstance(entry, dict) or entry.get("题目") != question:
        return None
    return entry.get("假设答案")


def put_frozen_answer(
    answers: dict[str, dict[str, str]], case_id: str, question: str, answer: str
) -> None:
    """记下冻结的答案 (连同它所回答的问题)."""
    answers[case_id] = {"题目": question, "假设答案": answer}


def save_hyde_answers(answers: dict[str, dict[str, str]]) -> Path:
    """
    把冻结的假设答案落盘.

    参数:
    - answers: case_id -> {"题目": ..., "假设答案": ...}

    返回值:
    - Path: 落盘位置
    """
    # 建**目标文件**的父目录, 而不是模块级的 ARTIFACTS_DIR —— 两者不一定同处
    # (测试里会把它指到 tmp, 生产上它就在 artifacts 下).
    HYDE_ANSWERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    HYDE_ANSWERS_FILE.write_text(
        json.dumps(answers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return HYDE_ANSWERS_FILE
