from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import JsonOutputParser

from ...infra.milvus import infra_milvus
from ...infra.model import infra_model
from ...process.query.agent.state import QueryState
from ...shared.clients.mongo_utils import get_recent_messages, save_chat_message
from ...shared.runtime.load_prompt import load_prompt
from ...shared.runtime.logger import logger, step_log
from .config import (
    ITEM_NAME_CANDIDATE_THRESHOLD,
    ITEM_NAME_CANDIDATE_TOPK,
    ITEM_NAME_CONFIRM_THRESHOLD,
    ITEM_NAME_CONFIRM_TOPK,
    QUERY_HISTORY_LIMIT,
)


@step_log("confirm_item_name")
def confirm_item_name(state: QueryState) -> QueryState:
    # 1.获取并校验参数
    original_query, session_id = _validate_and_get_data(state)

    # 2.获取有效的历史聊天记录
    histories = _get_history_by_session_id(session_id)

    # 3.调用LLM改写问题 + 提取item_names
    # 为什么改写
    #   替换代词
    #   去口语化
    #   专业化表达
    # 为什么提取item_names
    #   缩小检索范围
    #   明确要检索的文档
    #   如果不同的文档有相同的内容, 答案会混乱
    llm_result = _call_llm_rewritten_and_extract_itemnames(original_query, histories)

    # 4.如果 item_names 不为空, 检索向量数据库获取相关文档
    confirm_candidate_dict = {}
    if llm_result.get("item_names"):
        search_result = _select_item_names_milvus(llm_result.get("item_names"))
        confirm_candidate_dict = _select_confirm_candidate_item_names(search_result)

    # 5.更新状态
    _change_state_property(
        state,
        llm_result.get("rewritten_query"),
        confirm_candidate_dict,
    )

    # 6.写入历史聊天记录(用户提问)
    _save_user_chat_message(state)
    return state


@step_log("_validate_and_get_data")
def _validate_and_get_data(state: QueryState) -> tuple[str, str]:
    """获取并校验参数"""
    session_id = state.get("session_id")
    original_query = state.get("original_query")

    if not session_id or not original_query:
        logger.error("session_id / original_query 参数为空")
        raise ValueError("session_id / original_query 参数为空")

    return original_query, session_id


@step_log("_get_history_by_session_id")
def _get_history_by_session_id(session_id: str) -> list[dict]:
    """获取当前session_id对应的有效聊天记录"""

    histories: list[dict] = get_recent_messages(session_id, limit=QUERY_HISTORY_LIMIT)
    logger.debug(f"查询到聊天记录({session_id}): {len(histories)}")

    histories = [history for history in histories if history.get("item_names")]
    logger.debug(f"查询到有效的聊天记录({session_id}): {len(histories)}")

    return histories


@step_log("_call_llm_rewritten_and_extract_itemnames")
def _call_llm_rewritten_and_extract_itemnames(
    original_query: str, histories: list[dict]
) -> dict[str, Any]:
    """改写问题 + 提取item_names"""
    # 组装历史聊天记录的文本
    history_text: str = None
    if histories:
        history_text_list: list[str] = []

        for i, history in enumerate(histories, start=1):
            if history.get("role") == "user":
                # 对于用户的提问, 要记录原始问题 + 改写后的问题 + 关联的主体
                history_text_list.append(
                    f"编号: {i} (用户提问)\n"
                    f"原始问题: {history.get('text')},\n"
                    f"改写后的问题: {history.get('rewritten_query')},\n"
                    f"关联的item_names: {','.join(history.get('item_names'))}"
                )
            else:
                # 对于助手的回答, 要记录改写后的问题 + 回答结果 + 关联的主体
                # 回答结果需要部分截断, 防止稀释原本提示词中的问题/规则/格式
                history_text_list.append(
                    f"编号: {i} (助手回答)\n"
                    f"改写后的问题: {history.get('rewritten_query')}\n"
                    f"回答结果: {history.get('text')[:50]}\n"
                    f"关联的item_names: {','.join(history.get('item_names'))}"
                )
        history_text = "\n\n".join(history_text_list)
    else:
        history_text = "没有有效的历史聊天记录"

    # 加载提示词
    prompt_text: str = load_prompt(
        name="rewritten_query_and_itemnames",
        query=original_query,
        history_text=history_text,
    )
    message = HumanMessage(content=prompt_text)

    model = infra_model.llm_model(json_mode=True)
    chains = model | JsonOutputParser()
    llm_result: dict = chains.invoke([message])

    # 处理空值
    if "item_names" not in llm_result:
        llm_result["item_names"] = []
    if "rewritten_query" not in llm_result:
        llm_result["rewritten_query"] = original_query

    return llm_result


@step_log("_select_item_names_milvus")
def _select_item_names_milvus(item_names: list[str]):
    """
    根据模型识别的item_names查询向量数据库中的真实item_names

    Args:
        item_names: 模型识别的item_names
    Returns:
        dict[str, list[dict]]: 用模型识别的item_name检索到的真实item_name
    """
    milvus_search_result: dict[str, list[dict]] = {}

    # 1.将模型识别的item_names转换为向量
    src_vectors = infra_model.embedding(item_names)

    for i, src_item_name in enumerate(item_names):
        # 2.获取每个模型提供的item_name的稠密和稀疏向量
        dense_vector = src_vectors["dense"][i]
        sparse_vector = src_vectors["sparse"][i]

        # 3.使用向量生成查询 AnnSearchRequest
        reqs_list = infra_milvus.create_requests(
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            limit=5 * 2,  # 多路查询, 每一路10个
        )

        # 4.混合查询
        response = infra_milvus.hybrid_search(
            collection_name=infra_milvus.item_name_collection,
            reqs=reqs_list,
            ranker_weights=(0.4, 0.6),  # item_name检索, 更倾向于稀疏的权重
            norm_score=True,  # 归一化, 为了安全, 最好加上
            limit=5,  # 混合最后结果选出5个
            output_fields=["item_name"],  # 返回检索结果的 item_name 字段
        )

        # 5. 解析结果
        real_response = response[0]  # 混合检索只有1个query, 所以固定结果取第1个
        if not real_response:
            logger.warning(
                f"{src_item_name} 相似度检索结果为空, "
                f"即向量数据库为空, 中断接下来的所有查询"
            )
            break

        src_search_result: list[dict] = []
        for item in real_response:
            src_search_result.append(
                {
                    "item_name": item.get("entity", {}).get("item_name"),
                    "score": item.get("distance", 0.0),
                }
            )

        # 6.组装结果 dict {模型识别的item_name_1: [检索结果1, 检索结果2, ...], ...}
        milvus_search_result[src_item_name] = src_search_result

    return milvus_search_result


@step_log("_select_confirm_candidate_item_names")
def _select_confirm_candidate_item_names(milvus_dict):
    """从检索结果中提取确定和可选的列表"""
    confirm_list: list[str] = []  # 确定的item_name
    candidate_list: list[str] = []  # 可选的item_name

    # 遍历每个模型识别的item_name的检索结果
    for item_name, search_list in milvus_dict.items():
        conf_list = [
            item.get("item_name")
            for item in search_list
            if item.get("score", 0.0) >= ITEM_NAME_CONFIRM_THRESHOLD
        ]
        cand_list = [
            item.get("item_name")
            for item in search_list
            if ITEM_NAME_CANDIDATE_THRESHOLD
            <= item.get("score", 0.0)
            < ITEM_NAME_CONFIRM_THRESHOLD
        ]
        if conf_list:
            confirm_list += conf_list[:ITEM_NAME_CONFIRM_TOPK]
            logger.info(f"{item_name} 检测到确定主体: {confirm_list}")
            continue  # 既然有确定主体就不需要候选主体了
        if cand_list:
            candidate_list += cand_list[:ITEM_NAME_CANDIDATE_TOPK]
            logger.info(f"{item_name} 未检测到确定主体, 候选主体: {candidate_list}")

    return {
        "confirm": confirm_list,
        "candidate": candidate_list,
    }


@step_log("_change_state_property")
def _change_state_property(
    state: dict,
    rewritten_query: str,
    confirm_candidate_dict: dict,
):
    """
    更新state
    1.有确定的item_names: 更新 item_names + rewritten_query
    2.没有确定的但是有候选的item_names / 没有任何item_names: 仅更新 answer
    """
    confirm_list = confirm_candidate_dict.get("confirm", [])
    candidate_list = confirm_candidate_dict.get("candidate", [])

    if confirm_list:
        state["item_names"] = confirm_list
        state["rewritten_query"] = rewritten_query
        logger.info("已使用确定的item_names, 更新state (item_names + rewritten_query)")
        return

    if candidate_list:
        state["answer"] = f"未检测到明确的主体, 但有候选可供选择:\n{candidate_list}"
        logger.info("未检测到明确的主体, 已更新answer")
        return

    state["answer"] = "未检测到任何主体, 请向管理员确认知识库的内容"


@step_log("_save_user_chat_message")
def _save_user_chat_message(state: dict):
    """保存用户提问的聊天记录"""
    save_chat_message(
        session_id=state.get("session_id"),
        role="user",
        text=state.get("original_query"),
        rewritten_query=state.get("rewritten_query"),
        item_names=state.get("item_names", []),
        image_urls=[],
    )
