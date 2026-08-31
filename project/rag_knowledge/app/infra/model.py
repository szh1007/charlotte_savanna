from ..shared.model.embedding_utils import generate_embeddings
from ..shared.model.llm_utils import get_llm_client
from ..shared.model.reranker_utils import get_reranker_model


class InfraModel:
    def vision_model(self, vision_model_name: str):
        return get_llm_client(vision_model_name)

    def llm_model(self, llm_model_name: str | None = None, json_mode: bool = False):
        return get_llm_client(llm_model_name, json_mode)

    def reranker_model(self):
        return get_reranker_model()

    def embedding(self, texts: list[str]) -> dict[str, list]:
        return generate_embeddings(texts)

    def reranker_compute_scores(self, qa_pairs: list[list[str]]):
        return get_reranker_model().compute_score(qa_pairs, normalize=True)

    def reranker_compute_token_num(self, content: str):
        tokenizer = get_reranker_model().tokenizer
        token_list = tokenizer.encode(content, add_special_tokens=False)
        return len(token_list)


infra_model = InfraModel()

# from rich import print as rprint

# rprint(infra_model.vision_model("deepseek-v4-flash-vision-exp"))
# rprint(infra_model.llm_model())
