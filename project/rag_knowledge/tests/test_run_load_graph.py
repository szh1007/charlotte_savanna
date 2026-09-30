"""拿一份真实 PDF 手工跑加载全链路的**脚本**, 不是单元测试.

需要真实 MinerU / Milvus / 模型, 且此前在 import 期就执行 —— 作为 pytest 用例会在
**收集期**就炸 (issue C02 收口时改掉). `__test__ = False` 让 pytest 跳过整个模块;
要跑它仍然可以: `python -m tests.test_run_load_graph`.
"""

from app.process.load.agent.main_graph import graph
from app.process.load.agent.state import create_default_state
from app.shared.runtime.logger import PROJECT_ROOT, logger

# pytest 见到它就整模块跳过 (这是脚本, 不是用例)
__test__ = False

TEST_PDF_PATH = PROJECT_ROOT / "assets" / "hak180产品安全手册.pdf"


def run_load_graph() -> None:
    state = create_default_state(
        task_id="test_run_load_graph",
        local_file_path=str(TEST_PDF_PATH),
    )

    logger.info("------------------整体开始执行解析------------------\n")
    state = graph.invoke(state)
    logger.info("------------------整体执行解析结束------------------\n")


if __name__ == "__main__":
    run_load_graph()
