from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import step_log


@step_log("generate_chunk_embeddings")
def generate_chunk_embeddings(state: LoadState) -> LoadState:
    return state
