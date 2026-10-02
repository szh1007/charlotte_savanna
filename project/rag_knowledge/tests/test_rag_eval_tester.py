"""
RAG 评估系统最小调用测试.

这个文件只保留一种最简单的使用方式:

```python
from app.rag_eval import RagEvalTester

tester = RagEvalTester()
tester.run_eval()
```

前提: 知识库已由真实加载链路建好, 题库 `app/rag_eval/artifacts/eval_cases.json` 已就位.

如果后续把 `app/rag_eval` 复制到另一个项目,
这个测试文件也可以一起复制过去, 作为最小调用样例.
"""

from app.rag_eval import RagEvalTester

# pytest 见到它就整模块跳过 —— 这是「最小调用样例」脚本, 需要真实 Milvus / LLM / Mongo.
# 要跑它: `python -m tests.test_rag_eval_tester` (issue C02 收口时加的标记)
__test__ = False


def run_eval():
    """
    最简单的批量评测调用示例.

    返回值就是 `RagEvalTester.run_eval()` 的原始返回结果.
    """
    tester = RagEvalTester()
    return tester.run_eval()


def demo():
    """演示完整调用: 直接跑批量评测."""
    eval_result = run_eval()
    print("批量评测结果:", eval_result)


if __name__ == "__main__":
    demo()
