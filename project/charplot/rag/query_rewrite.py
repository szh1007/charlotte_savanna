"""Query rewriting (Issue 10, SPEC §7.2) - 检索前 LLM 改写.

改写目标: 口语化/简短查询 → 利于向量检索的完整表述 (补充实体与上下文,
提升稠密/稀疏召回质量). 失败语义: LLM 不可用/超时/输出非法 → 返回
原 query (降级不阻塞检索, rewrite 是增强不是强依赖).

LLM 调用走 pipeline.llm.get_chat_model (与解构/出题同款 DeepSeek 单例,
测试经 conftest 注入 FakeChatModel). 配置 CHARPLOT_QUERY_REWRITE=false
可整体关闭.
"""

import logging

from ..api import config
from ..pipeline import llm

logger = logging.getLogger(__name__)

_REWRITE_PROMPT = """你是检索查询改写器. 把用户查询改写为更适合向量检索的
完整表述: 补全省略的实体/主题词, 用清晰名词短语表达检索意图. 只输出改写
结果, 不要解释.

原查询: {query}
改写后:"""

_MAX_QUERY_LENGTH = 200


def rewrite_query(query: str) -> str:
    """改写查询 (失败/关闭时返回原 query, 不抛异常)."""
    if not config.QUERY_REWRITE:
        return query
    prompt = _REWRITE_PROMPT.format(query=query.strip())
    try:
        model = llm.get_chat_model()
        response = model.invoke(prompt)
        rewritten = str(response.content).strip()
    except Exception as exc:
        logger.warning("query rewriting 失败, 降级原查询: %s", exc)
        return query
    # 输出非法 (空/过长/复述原文) 也降级原查询
    if not rewritten or rewritten.lower() == query.strip().lower():
        return query
    if len(rewritten) > _MAX_QUERY_LENGTH:
        rewritten = rewritten[:_MAX_QUERY_LENGTH]
    logger.info("query rewriting: %r → %r", query, rewritten)
    return rewritten
