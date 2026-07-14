import dotenv
from langchain.chat_models import init_chat_model

dotenv.load_dotenv()

llm = init_chat_model(
    model="deepseek:deepseek-v4-pro",
    temperature=0.7,
    max_tokens=1024,
    timeout=60,
    max_retries=3,
    model_kwargs={},  # OpenAI Compatible API 支持, 但是 LangChain 没有列出来的字段
    extra_body={},  # 厂商基于 OpenAI API 协议扩展的字段
)

# fprint(llm.profile)
# fprint(llm.model_fields.keys())

""" invoke config """
# run_name: LangSmith 识别不同的运行任务
# tags: LangSmith 识别的同时, 用于分类和过滤
# callbacks: 设置回调处理器, LangSmith 深度追踪和调试
# metadata: 本次调用的上下文元数据
# max_concurrency: 最大并行数
# configurable
#   1.与 init_chat_model 的参数一致, 但是可以在 invoke 时覆盖 init_chat_model 的参数
#   2.但是前提必须在 init_chat_model 设置 configurable_fields = ("model", ...)
