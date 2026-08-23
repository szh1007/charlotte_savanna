"""
工具模块, 负责提供 llm 相关的辅助能力.
"""

from langchain_core.exceptions import LangChainException
from langchain_openai import ChatOpenAI

from ..config.llm_config import llm_config
from ..runtime.logger import logger

_DEFAULT_LLM_MODEL = "qwen3-32b"
_DEFAULT_TEMPERATURE = 0.1
_llm_client_cache: dict[tuple[str, bool], ChatOpenAI] = {}


def get_llm_client(model: str | None = None, json_mode: bool = False) -> ChatOpenAI:
    """
    获取带全局缓存的 LangChain ChatOpenAI 客户端实例
    适配 OpenAI/千问/即梦 AI 等**OpenAI 兼容 API**, 支持自定义模型和 JSON 标准化输出
    核心特性: 缓存机制 + 配置统一加载 + 异常精准捕获 + 国产模型参数适配

    :param model: 模型名称,
                  优先级: 传入参数 > 配置文件 llm_config.llm_model > 内置默认模型
    :param json_mode: 是否开启 JSON 输出模式, 开启后返回标准 json_object 格式
    :return: 初始化完成的 ChatOpenAI 实例(优先从全局缓存获取, 未命中则新建并缓存)

    :raise ValueError: 缺失 API 密钥/基础地址等核心配置
    :raise Exception: 模型初始化失败(LangChain 封装层异常)
    """
    # 1. 确定目标模型(优先级递减, 保证模型名非空)
    target_model = model or llm_config.llm_model or _DEFAULT_LLM_MODEL

    # 缓存键: 模型名 + JSON 模式, 唯一标识不同配置的客户端
    cache_key = (target_model, json_mode)

    # 2. 缓存命中: 直接返回已初始化的实例, 避免重复创建
    if cache_key in _llm_client_cache:
        logger.debug(
            f"[LLM客户端] 缓存命中, "
            f"直接返回实例: 模型={target_model}, JSON 模式={json_mode}"
        )
        return _llm_client_cache[cache_key]

    # 3. 核心配置校验: 拦截缺失的 API 关键配置, 提前抛出明确异常
    if not llm_config.api_key:
        raise ValueError(
            "[LLM客户端] 配置缺失: 请在 .env 中配置 OPENAI_API_KEY(大模型 API 密钥)"
        )
    if not llm_config.base_url:
        raise ValueError(
            "[LLM客户端] 配置缺失: 请在 .env 中配置 OPENAI_BASE_URL(API 接口基础地址)"
        )
    logger.info(
        f"[LLM客户端] 开始初始化新实例: 模型={target_model}, JSON 模式={json_mode}"
    )

    # 4. 配置参数组装: 区分[国产模型私有参数]和[OpenAI 通用参数]
    # extra_body: 千问/即梦等国产模型专属私有参数(LangChain 透传至 API)
    extra_body = {"enable_thinking": False}  # 千问专属: 关闭思考链输出, 减少冗余内容

    # model_kwargs: OpenAI 通用参数, 所有兼容 API 均支持
    model_kwargs = {}
    if json_mode:
        # 开启 JSON 标准输出模式, 强制模型返回可解析的 json_object
        model_kwargs["response_format"] = {"type": "json_object"}
        logger.debug("[LLM客户端] 已开启 JSON 输出模式, 模型将返回标准 JSON 结构")

    # 5. 客户端初始化: 捕获 LangChain 封装层异常, 抛出更友好的提示
    try:
        llm_client = ChatOpenAI(
            model=target_model,  # 目标模型名
            temperature=llm_config.llm_temperature
            or _DEFAULT_TEMPERATURE,  # 低温度保证输出确定性(0~1)
            api_key=llm_config.api_key,  # API 密钥
            base_url=llm_config.base_url,  # API 基础地址(适配国产模型代理地址)
            extra_body=extra_body,  # 国产模型私有参数透传
            model_kwargs=model_kwargs,  # OpenAI 通用参数
        )
    except LangChainException as e:
        raise Exception(
            f"[LLM客户端] 模型[{target_model}]初始化失败(LangChain 层): {e!s}"
        ) from e

    # 6. 新实例存入全局缓存, 供后续调用复用
    _llm_client_cache[cache_key] = llm_client
    logger.info(
        f"[LLM客户端] 实例初始化成功并缓存: 模型={target_model}, JSON 模式={json_mode}"
    )

    return llm_client
