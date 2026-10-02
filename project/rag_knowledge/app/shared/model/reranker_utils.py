"""
工具模块, 负责提供 reranker 相关的辅助能力.
"""

from FlagEmbedding import FlagReranker

from ..config.reranker_config import reranker_config
from ..runtime.logger import logger

_reranker_model: FlagReranker | None = None


def get_reranker_model() -> FlagReranker:
    """
    获取重排模型单例对象.

    Returns:
        FlagReranker: 初始化完成的重排模型实例.
    """
    global _reranker_model
    if _reranker_model is None:
        logger.info(
            "开始初始化重排模型, 最大输入长度: "
            f"{reranker_config.bge_reranker_max_length}"
        )
        _reranker_model = FlagReranker(
            model_name_or_path=reranker_config.bge_reranker_large,
            device=reranker_config.bge_reranker_device,
            use_fp16=reranker_config.bge_reranker_fp16,
            # 不传就是 512 —— 中文 chunk 会被从中间截断, 上游也因此不得不先做一次
            # LLM 压缩来迁就窗口. 窗口开到 2048 (见 reranker_config 的说明) 之后,
            # 候选可以整条送进来.
            max_length=reranker_config.bge_reranker_max_length,
        )
        # FlagReranker 组装 batch 走的是 encode + pad, transformers 会劝它改成
        # __call__ —— 那是给「自己写批处理」的人看的性能建议, 第三方库改不了,
        # 每个进程启动都要刷一条. 用 transformers 自己的去重表把它标成「已提示」,
        # 比在日志层做拦截干净 (库在 _pad 里就是查这个 key).
        _reranker_model.tokenizer.deprecation_warnings[
            "Asking-to-pad-a-fast-tokenizer"
        ] = True
        logger.success("重排模型初始化成功")
    return _reranker_model
