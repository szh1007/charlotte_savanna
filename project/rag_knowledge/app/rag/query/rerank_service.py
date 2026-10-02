import re

from ...infra.model import infra_model
from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import logger, step_log
from .config import (
    DOC_HEAD_SCORE_FACTOR,
    ENABLE_RERANK,
    RERANK_FUSION_ALPHA,
    RERANK_GAP_ABS,
    RERANK_GAP_RATIO,
    RERANK_LENGTH_TIERS,
    RERANK_MAX_TOPK,
    RERANK_MIN_TOPK,
)

# 文档标题片: title 是文档 H1 (`# 项目名`), 区别于章节标题的 `##` / `###`.
_DOC_HEAD_TITLE_REGEX = re.compile(r"^#\s")


def _is_document_head(chunk: dict) -> bool:
    """判断这一条是不是「文档标题片」—— 文档开头那块 `# 项目名` + 项目简介.

    它是全库唯一同时满足「含项目名」和「通篇讲这个项目」的块, 因此与任何带
    主体名的问题字面重合度都最高. 实测 40 题里 35 题它都进了候选池, 但真正
    拿它当答案的只有 2 题 (「XX 是什么项目」).

    降权而不排除的理由见 `DOC_HEAD_SCORE_FACTOR`.
    """
    return bool(_DOC_HEAD_TITLE_REGEX.match(chunk.get("title") or ""))


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

    # 2.6 精排开关关闭时按 RRF 原顺序出结果 (实测精排在此数据集上是负优化, 见 config)
    if not ENABLE_RERANK:
        logger.info("精排已关闭 (ENABLE_RERANK=False), 按 RRF 原顺序截取候选")
        state["reranked_docs"] = merged_list[:RERANK_MAX_TOPK]
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
                # RRF 的分数单独留一份: 精排的分数会覆盖 score, 但那条排序本身
                # 是个很准的先验 (实测 MRR@1 = 0.70), 融合时要用 (见
                # _list_score_and_rank).
                "rrf_score": chunk.get("score", 0.0),
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
                # 联网这一路不参与 RRF, 没有 RRF 先验可给
                "rrf_score": 0.0,
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
    """把问题与候选正文配成 reranker 要的 (query, passage) 对.

    **这里不做任何加工**: 超过窗口的候选交给 reranker 自己按 `max_length` 截断.

    此前会先调 LLM 把长候选"精炼"一遍再送去打分, 那是给 512 窗口打的补丁 ——
    实测 67% 的候选都会被压 (chunk 中位 674 token vs 窗口 512). 代价是:
    **评测按原文标 gold, reranker 却在压缩稿上判分**; 而且同一道题每次跑,
    LLM 压出来的稿子不一样, 分数就跟着变. 窗口开到 2048 之后当前的语料已经
    装得下 (最长约 1250 token), 真的超了也让模型截断 —— 截断是确定的,
    压缩不是.
    """
    return [[rewritten_query, chunk.get("text")] for chunk in merged_list]


@step_log("_pick_rerank_max_length")
def _pick_rerank_max_length(question_answer_pair: list[list[str]]) -> int:
    """按本批候选里最长的一条选窗口档位.

    窗口决定 padding 宽度, 而 reranker 的 attention 是平方复杂度 —— 给一批
    最长 900 token 的候选按 2048 跑, 多出来的都是 pad 出来的无效计算.
    超过最高档也不报错: 多出来的部分由模型按窗口截断 —— 截断是确定的, 不会
    像 LLM 压缩那样每次跑出不一样的结果.
    """
    longest = 0
    for question, answer in question_answer_pair:
        longest = max(
            longest,
            infra_model.reranker_compute_token_num(f"{question}{answer}"),
        )

    for tier in RERANK_LENGTH_TIERS:
        if longest <= tier:
            return tier
    return RERANK_LENGTH_TIERS[-1]


@step_log("_list_score_and_rank")
def _list_score_and_rank(
    merged_list: list[dict],
    question_answer_pair: list[list[str]],
):
    """reranker 打分, 与 RRF 先验融合后排序.

    只按精排分数排等于「一票否决」: reranker 打偏时没有任何兜底, 而它在中文长
    文本上确实会偏. 融合权重见 `RERANK_FUSION_ALPHA`; 文档标题片在融合之后
    再打一次折, 见 `DOC_HEAD_SCORE_FACTOR`.
    """
    max_length = _pick_rerank_max_length(question_answer_pair)
    logger.info(f"本批精排窗口档位: {max_length} token")
    score_list = infra_model.reranker_compute_scores(
        question_answer_pair, max_length=max_length
    )

    # RRF 分数和 rerank 分数不同量级 (前者是 1/(60+rank) 的累加, 只有 0.008~0.017),
    # 不归一化就相加等于没融合 —— rerank 那一项会把 RRF 彻底盖住.
    #
    # 归一化用「除以最大值」而不是 min-max: min-max 会把最小的强行拉到 0、最大的
    # 拉到 1, 把 RRF 的跨度**撑满**; 于是候选少的时候 (两个元素就是 1.0 vs 0.0)
    # 精排再准也翻不了盘. RRF 分数本身就是有意义的相对值, 按比例缩到 (0, 1] 即可.
    rrf_scores = [chunk.get("rrf_score", 0.0) for chunk in merged_list]
    rrf_high = max(rrf_scores, default=0.0)

    # merged_list -> question_answer_pair -> 顺序和数量一致
    # question_answer_pair -> score_list -> 顺序和数量一致
    # 因此 merged_list 和 score_list 顺序和数量一致
    for score, chunk in zip(score_list, merged_list):
        chunk["rerank_score"] = score
        # 分数全是 0 时 (比如只有联网结果) 没有「谁更靠前」可言, 给满值让这一项
        # 退化成常数, 不干扰排序
        rrf_norm = chunk.get("rrf_score", 0.0) / rrf_high if rrf_high else 1.0
        if chunk.get("type") == "web_search":
            # 联网那一路没进 RRF, rrf_score 是占位的 0 —— 按比例缩会把它压到最低
            # (等于系统性降权), 给个中性值: 与 RRF 里偏低的那一档持平
            rrf_norm = 0.5

        fused = RERANK_FUSION_ALPHA * rrf_norm + (1 - RERANK_FUSION_ALPHA) * score
        # 文档标题片打折 (折扣写在排序前, 断崖看到的也是折后分 —— 它本就
        # 不该占着高位把具体章节挤到断崖之下)
        if _is_document_head(chunk):
            fused *= DOC_HEAD_SCORE_FACTOR
        chunk["score"] = fused

    logger.debug(f"未排序之前的合并列表: {merged_list}")
    merged_list.sort(key=lambda c: c.get("score", 0.0), reverse=True)
    logger.debug(f"未排序之后的合并列表: {merged_list}")


@step_log("_dynamic_topk")
def _dynamic_topk(merged_list: list[dict]):
    """动态截取目标内容

    截取上限是 `RERANK_MAX_TOPK`, 下限 `RERANK_MIN_TOPK` —— 没找到断崖点就返回上限条.
    断崖检测从下限处开始向后看 —— 所以下限**不能**等于上限: 曾经两者都是 8,
    结果是「前 8 条无条件保留」, 8 条里分数从 0.95 掉到 0.05 也照样原样返回.

    **判据是「相对头部的累计衰减」, 不是「相邻两点的落差」**: 0.8 / 0.7 / 0.6 / 0.5
    这样的序列每步只掉 0.1, 相邻比较永远不触发断崖, 可到第 3 个已经比头部低了 25%
    —— 那正是该断的地方. 现在拿每个位置的分数和**第 1 名**比: 跌破
    `head - RERANK_GAP_ABS` 或 `head * (1 - RERANK_GAP_RATIO)` 就在那里断开.
    """
    # 列表长度可能小于max_topk
    max_topk = min(RERANK_MAX_TOPK, len(merged_list))
    min_topk = RERANK_MIN_TOPK

    # 当前截取位置(防止没有断崖处)
    top_k = max_topk

    # 列表长度可能小于min_topk
    if max_topk > min_topk and merged_list:
        head_score = merged_list[0].get("score", 0.0)

        # 从第 min_topk 个开始检查 (前 min_topk 个保底留下)
        for i in range(min_topk, max_topk):
            current_score = merged_list[i].get("score", 0.0)

            # 相对头部的累计衰减
            gap = head_score - current_score
            # 头部为 0 时没有「百分比」可言 (分数已归一化到 0~1, 头部为 0
            # 意味着后面全是 0, 不存在断崖) —— 此前直接相除会 ZeroDivisionError
            # (issue C02)
            ratio = gap / head_score if head_score else 0.0

            if gap > RERANK_GAP_ABS or ratio > RERANK_GAP_RATIO:
                # 这一条已经跌出头部区间, 从这里断开
                top_k = i
                logger.info(
                    f"下标{i}位置的分数相对头部衰减过大, 在此断开\n"
                    f"头部: {head_score}\n"
                    f"当前: {current_score}\n"
                    f"绝对差: {gap}\n"
                    f"相对差: {ratio * 100:.2f}%\n"
                )
                break

    return merged_list[:top_k]
