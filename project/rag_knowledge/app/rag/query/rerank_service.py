from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser

from ...infra.model import infra_model
from ...process.query.agent.state import QueryState
from ...shared.runtime.load_prompt import load_prompt
from ...shared.runtime.logger import logger, step_log
from .config import (
    RERANK_GAP_ABS,
    RERANK_GAP_RATIO,
    RERANK_MAX_INPUT_TOKENS,
    RERANK_MAX_TOPK,
    RERANK_MIN_SUMMARY_CHARS,
    RERANK_MIN_TOPK,
    RERANK_SUMMARY_CHAR_RATIO,
)


@step_log("rerank_documents")
def rerank_documents(state: QueryState) -> QueryState:
    # 1.获取并且校验参数
    rewritten_query, rrf_chunks, web_search_docs = _validate_data(state)

    # 2.对齐数据格式
    merged_list = _merge_rrf_and_web(rrf_chunks, web_search_docs)

    # 2.5 两路都没东西可精排 —— 直接给空结果, 别把空列表喂给打分模型
    if not merged_list:
        logger.warning("两路召回都为空, 跳过精排, reranked_docs 置空")
        state["reranked_docs"] = []
        return state

    # 3.封装问题+答案的配对
    question_answer_pair = _create_question_answer_pair(rewritten_query, merged_list)

    # 4.进行内容打分和排序
    _list_score_and_rank(merged_list, question_answer_pair)

    # 5.进行动态内容截取(断崖检测)
    reranked_docs = _dynamic_topk(merged_list)

    state["reranked_docs"] = reranked_docs
    return state


@step_log("_validate_data")
def _validate_data(state: QueryState):
    """获取参数.

    只有 `rewritten_query` 是**真的必需** —— 没有它就没有可用来打分的问句.
    两路召回结果为空都不是错误: 单路空 = 这一路没贡献, 全空 = 这次检索不到东西,
    两者都该走到作答节点的「不知道」分支, 而不是把整条链变成 500 (issue C02).
    """
    rewritten_query = state.get("rewritten_query")
    rrf_chunks = state.get("rrf_chunks") or []
    web_search_docs = state.get("web_search_docs") or []

    if not rewritten_query:
        logger.error("rewritten_query 参数为空")
        raise ValueError("rewritten_query 参数为空")

    return rewritten_query, rrf_chunks, web_search_docs


@step_log("_merge_rrf_and_web")
def _merge_rrf_and_web(
    rrf_chunks: list[dict],
    web_search_docs: list[dict],
):
    """两路数据融合, 统一数据结构"""
    merged_list: list = []

    for chunk in rrf_chunks:
        merged_list.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "title": chunk.get("title"),
                "text": chunk.get("content"),
                "score": 0.0,
                "type": chunk.get("type"),
                "url": "",
            }
        )
    for doc in web_search_docs:
        merged_list.append(
            {
                "chunk_id": "",
                "title": doc.get("title"),
                "text": doc.get("text"),
                "score": 0.0,
                "type": "web_search",
                # url 必须原样带过来: 它此前被硬编码成空串, 于是答案里写着
                # 「来源: 联网搜索」却给不出链接 —— 「可溯源」在 web 这一路是空的
                # (issue C02).
                # (score 保持 0.0 是有意的: 它马上会被 reranker 的分数覆盖,
                #  两路在这里只是先对齐形状.)
                "url": doc.get("url", ""),
            }
        )

    logger.info(
        f"RRF + WebSearch 数据格式已融合统一: "
        f"{len(rrf_chunks)}+{len(web_search_docs)}={len(merged_list)}"
    )
    return merged_list


@step_log("_create_question_answer_pair")
def _create_question_answer_pair(
    rewritten_query: str,
    merged_list: list[dict],
) -> list[list[str]]:
    """
    问题: rewritten_query
    答案: merged_list: list[dict] -> list[list[str]]
    """
    question_token_len = infra_model.reranker_compute_token_num(rewritten_query)

    question_answer_pair: list[list[str]] = []

    for chunk in merged_list:
        answer = chunk.get("text")
        answer_token_len = infra_model.reranker_compute_token_num(answer)

        # 4 = 4个问题和答案的分隔符
        answer_max_token_len = RERANK_MAX_INPUT_TOKENS - 4 - question_token_len

        if answer_token_len > answer_max_token_len:
            char_length = max(
                int(answer_max_token_len / RERANK_SUMMARY_CHAR_RATIO),
                RERANK_MIN_SUMMARY_CHARS,
            )

            rerank_text: str = load_prompt(
                "rerank_text_refine",
                question=rewritten_query,
                answer=answer,
                limit=char_length,
            )
            chains = infra_model.llm_model() | StrOutputParser()

            logger.debug(f"已经触发压缩流程, 压缩前{len(answer)}:{answer}")
            answer = chains.invoke([HumanMessage(rerank_text)])
            logger.debug(f"已经触发压缩流程, 压缩后{len(answer)}:{answer}")

        question_answer_pair.append([rewritten_query, answer])

    return question_answer_pair


@step_log("_list_score_and_rank")
def _list_score_and_rank(
    merged_list: list[dict],
    question_answer_pair: list[list[str]],
):
    """reranker 打分 + 排序"""
    score_list = infra_model.reranker_compute_scores(question_answer_pair)

    # merged_list -> question_answer_pair -> 顺序和数量一致
    # question_answer_pair -> score_list -> 顺序和数量一致
    # 因此 merged_list 和 score_list 顺序和数量一致
    for score, chunk in zip(score_list, merged_list):
        chunk["score"] = score

    logger.debug(f"未排序之前的合并列表: {merged_list}")
    merged_list.sort(key=lambda c: c.get("score", 0.0), reverse=True)
    logger.debug(f"未排序之后的合并列表: {merged_list}")


@step_log("_dynamic_topk")
def _dynamic_topk(merged_list: list[dict]):
    """动态截取目标内容"""
    # 列表长度可能小于max_topk
    max_topk = min(RERANK_MAX_TOPK, len(merged_list))
    min_topk = RERANK_MIN_TOPK

    # 当前截取位置(防止没有断崖处)
    top_k = max_topk

    # 列表长度可能小于min_topk
    if max_topk > min_topk:
        # 从 min_topk 开始检查断崖
        for i in range(min_topk - 1, max_topk - 1):
            current_chunk, next_chunk = merged_list[i], merged_list[i + 1]
            current_score = current_chunk.get("score", 0.0)
            next_score = next_chunk.get("score", 0.0)

            # 计算绝对插值和百分比差值
            gap = current_score - next_score
            # 分母为 0 时没有「百分比」可言 (分数已归一化到 0~1, 当前项为 0
            # 意味着后面全是 0, 不存在断崖) —— 此前直接相除会 ZeroDivisionError
            # (issue C02)
            ratio = gap / current_score if current_score else 0.0

            if gap > RERANK_GAP_ABS or ratio > RERANK_GAP_RATIO:
                # 断崖处
                top_k = i + 1
                logger.debug(
                    f"下标{i}位置发生断崖\n"
                    f"当前值: {current_score}\n"
                    f"下一个值: {next_score}"
                )
                break

    return merged_list[:top_k]
