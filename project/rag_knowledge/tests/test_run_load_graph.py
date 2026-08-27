from ..app.process.load.agent.main_graph import graph
from ..app.process.load.agent.state import create_default_state
from ..app.shared.runtime.logger import PROJECT_ROOT, logger

test_pdf_path = PROJECT_ROOT / "assets" / "hak180产品安全手册.pdf"

state = create_default_state(
    task_id="test_run_load_graph",
    local_file_path=str(test_pdf_path),
)

logger.info("------------------整体开始执行解析------------------\n")
state = graph.invoke(state)
logger.info("------------------整体执行解析结束------------------\n")
