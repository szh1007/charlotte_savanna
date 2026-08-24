from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import step_log


@step_log("parse_pdf_to_markdown")
def parse_pdf_to_markdown(state: LoadState) -> LoadState:
    return state
