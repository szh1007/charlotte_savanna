from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import step_log


@step_log("resolve_input_file")
def resolve_input_file(state: LoadState) -> LoadState:
    return state
