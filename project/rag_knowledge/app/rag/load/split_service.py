from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import step_log


@step_log("split_document")
def split_document(state: LoadState) -> LoadState:
    return state
