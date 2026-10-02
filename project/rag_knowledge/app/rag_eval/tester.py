"""
RAG 评估测试类.

这个文件故意写得很薄.
你只需要记住一个类和一个方法:

- `RagEvalTester.run_eval()`

其他具体流程都放在 `runner.py` 里.

前提: **知识库已经由真实加载链路 (`load_graph`) 建好, 题库也已经就位** ——
评测包不负责导数据, 只负责拿题库去打这条已经跑起来的检索链路.
"""


class RagEvalTester:
    """
    RAG 评估统一入口类.

    最简单用法:

    ```python
    from app.rag_eval import RagEvalTester

    tester = RagEvalTester()
    tester.run_eval()
    ```
    """

    def run_eval(self) -> dict:
        """
        运行批量评测.

        返回值包含:
        - eval_results: 每条问题的详细评测结果
        - summary: 批量汇总结果
        - report_path: 评测报告文件路径
        """
        from app.rag_eval.runner import close_mongo_client, run_batch_eval

        try:
            return run_batch_eval()
        finally:
            close_mongo_client()
