"""Query rewriting (L5-a): 检索前把口语化查询改写成利于向量检索的表述.

搬运自 `project/charplot/rag/query_rewrite.py`, 换掉的是**模型从哪来**: charplot 绑
自己的 `pipeline.llm.get_chat_model` (一个模块级单例), 这里改成**调用方传进来**的
`CharAgent` `ChatModel` —— 会话手里那一个. 于是改写用的是同一套模型配置 (重试 /
熔断 / 计费都在它身上), 不需要这条链路自己再读一份 API Key.

**降级语义整段保留**: 改写是增强, 不是前提. 开关关掉、模型没给、超时、输出为空 /
复述原文 —— 这几种都返回**原查询**, 不抛异常、不阻塞检索; 输出太长则**截断后使用**
(截断处仍是改写结果, 丢掉的是尾巴). 失败只留一条 warning, 因为一次改写失败不该让
买家听到任何异常.

改写调用刻意关掉思考模式 (`thinking=False`) 并限了输出长度: 这是一个"把
「能退吗」补成「退款的条件与流程是什么」"的格式化任务, 不值得为它烧思维链 token,
也不该让它有机会输出一整段解释 (上限 200 字, 超了截断).
"""

from __future__ import annotations

import logging

from CharAgent.model.protocol import ChatModel
from CharApp.minimall.config import KnowledgeConfig

logger = logging.getLogger(__name__)

_REWRITE_PROMPT = """你是检索查询改写器. 把用户查询改写为更适合向量检索的
完整表述: 补全省略的实体/主题词, 用清晰名词短语表达检索意图. 只输出改写
结果, 不要解释.

原查询: {query}
改写后:"""

# 改写结果的长度上限 (字符): 超过就截断 —— 检索词不需要长
_MAX_QUERY_LENGTH = 200

# 输出 token 上限: 200 个中文字符远用不到这个数, 它是防"模型开始解释"的闸
_MAX_OUTPUT_TOKENS = 512


async def rewrite_query(
    query: str, *, model: ChatModel | None, config: KnowledgeConfig
) -> str:
    """改写查询 (关闭/没给模型/任何失败 → 返回原 query, 不抛异常).

    Args:
        query: 买家的原话 (或模型自己组织的检索词).
        model: 会话手里的模型; None = 这次不改写 (索引脚本 / 没有模型的场景).
        config: 知识库配置 (`query_rewrite` 是总开关).

    Returns:
        str: 改写后的查询; 任何一处不成立都是原查询.
    """
    original = query.strip()
    if not config.query_rewrite or model is None:
        return original
    prompt = _REWRITE_PROMPT.format(query=original)
    try:
        response = await model.generate(
            [{"role": "user", "content": prompt}],
            thinking=False,
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
        rewritten = (response.content or "").strip()
    except Exception as exc:  # 任何失败都降级, 理由见模块 docstring
        logger.warning("query rewriting 失败, 降级原查询: %s", exc)
        return original
    # 输出非法 (空 / 复述原文) 也降级原查询
    if not rewritten or rewritten.lower() == original.lower():
        return original
    if len(rewritten) > _MAX_QUERY_LENGTH:
        rewritten = rewritten[:_MAX_QUERY_LENGTH]
    logger.info("query rewriting: %r → %r", original, rewritten)
    return rewritten


__all__ = ["rewrite_query"]
