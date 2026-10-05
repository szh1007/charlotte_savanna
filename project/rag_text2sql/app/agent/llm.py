from langchain.chat_models import init_chat_model

from app.conf.app_config import app_config

# C16: 显式给 LLM 调用一个预算.
#
# 为什么必须有 (2026-10 实测): 不配置时 SDK 默认读超时 600s, 等于没有预算 ——
# C15 跑批 117 次里有 2 次卡在取值召回的 LLM 调用上, 请求一直飞, 240s 被跑批器
# 兜底掐掉; 生产链路上则是一个请求挂 10 分钟. 现在:
#   - timeout: 单次请求 60s 上限 (实测正常 2~20s, 留了 3 倍余量)
#   - max_retries: 交给 OpenAI SDK, 它的判据与 C16 票面一致 —— 只重瞬态
#     (429 / 5xx / 连接失败 / 超时; 外加 408 / 409 这两个幂等可重的),
#     其余 4xx 直接放弃; 取消 (CancelledError) 不是 Exception, 不会被它吞掉
llm = init_chat_model(
    model=app_config.llm.model_name,
    api_key=app_config.llm.api_key,
    temperature=0,
    timeout=app_config.llm.timeout_s,
    max_retries=app_config.llm.max_retries,
)


if __name__ == "__main__":
    for chunk in llm.stream("你的底层模型是什么"):
        print(chunk.text, end="", flush=True)
