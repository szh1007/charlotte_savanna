from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import JsonOutputParser

from ...infra.model import infra_model
from ...process.query.agent.state import QueryState
from ...shared.clients.mongo_utils import get_recent_messages
from ...shared.runtime.load_prompt import load_prompt
from ...shared.runtime.logger import logger, step_log


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
    return state, llm_result


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

    histories: list[dict] = get_recent_messages(session_id, limit=10)
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
