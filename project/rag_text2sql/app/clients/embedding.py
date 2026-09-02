import asyncio

from langchain_openai import OpenAIEmbeddings

from app.conf.app_config import EmbeddingConfig, app_config


class EmbeddingClient:
    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.embeddings: OpenAIEmbeddings | None = None

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}/v1"

    def init(self):
        """
        model
            docker 服务启动时已通过 model_id 加载模型, 此处仅占位
        api_key
            TEI 本地服务无鉴权, api_key 传占位符即可
        check_embedding_ctx_length
            TEI 不接受 tiktoken token ID, 需关闭长度校验走原始文本
        """
        self.embeddings = OpenAIEmbeddings(
            base_url=self._get_url(),
            model=self.config.model,
            api_key="not-needed",
            check_embedding_ctx_length=False,
        )


embedding_client = EmbeddingClient(app_config.embedding)


if __name__ == "__main__":
    # 初始化并获取客户端对象
    embedding_client.init()
    embeddings = embedding_client.embeddings

    async def test():
        text = "What is deep learning?"
        result = await embeddings.aembed_documents([text])

        print(type(result), len(result))
        print(type(result[0]), len(result[0]))
        print(f"{result[0][:5]}...")

    asyncio.run(test())
