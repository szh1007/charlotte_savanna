from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import step_log


@step_log("index_chunks")
def index_chunks(state: LoadState) -> LoadState:
    return state
