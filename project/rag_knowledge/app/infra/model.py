from ..shared.model.embedding_utils import generate_embeddings
from ..shared.model.llm_utils import get_llm_client


class InfraModel:
    def vision_model(self, vision_model_name: str):
        return get_llm_client(vision_model_name)

    def llm_model(self, llm_model_name: str | None = None, json_mode: bool = False):
        return get_llm_client(llm_model_name, json_mode)

    def embedding(self, texts: list[str]) -> dict[str, list]:
        return generate_embeddings(texts)


infra_model = InfraModel()

# from rich import print as rprint

# rprint(infra_model.vision_model("deepseek-v4-flash-vision-exp"))
# rprint(infra_model.llm_model())
