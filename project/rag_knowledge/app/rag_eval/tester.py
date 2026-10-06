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

    def run_eval(
        self,
        capture_hyde: bool = False,
        rerank: bool = True,
        report_name: str | None = None,
    ) -> dict:
        """
        运行批量评测.

        参数:
        - capture_hyde: 抓取模式 —— 冻结文件里缺的题现场生成 HyDE 假设答案并落盘.
          题库新增题目后先跑一次, 之后正式跑都走冻结重放.
        - rerank: 精排开关, 消融的另一臂就靠它.
        - report_name: 报告文件名; 不传时自动带时间戳.

        返回值包含:
        - eval_results: 每条问题的详细评测结果
        - summary: 批量汇总结果
        - report_path: 评测报告文件路径
        - run_config: 这一趟的运行配置
        """
        from app.rag_eval.runner import close_mongo_client, run_batch_eval

        try:
            return run_batch_eval(
                capture_hyde=capture_hyde,
                rerank=rerank,
                report_name=report_name,
            )
        finally:
            close_mongo_client()
