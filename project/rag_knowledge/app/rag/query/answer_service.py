import re

from ...infra.model import infra_model
from ...process.query.agent.state import QueryState
from ...shared.clients.mongo_utils import get_recent_messages, save_chat_message
from ...shared.runtime.load_prompt import load_prompt
from ...shared.runtime.logger import logger, step_log
from ...shared.utils.sse_utils import SSEEvent, push_to_session
from .config import QUERY_HISTORY_LIMIT


@step_log("generate_answer")
def generate_answer(state: QueryState) -> QueryState:
    # 1.判断是否已有answer
    has_answer: bool = _answer_exists_in_state(state)

    # 2.answer不存在, 正常走流程
    if not has_answer:
        # 2.1 获取并校验参数
        session_id, item_names, rewritten_query, reranked_docs, is_stream = (
            _validate_data(state)
        )

        # 2.2 获取有效的聊天记录
        histories = _get_history_by_session_id(session_id)

        # 2.3 拼接总提示词
        answer_prompt = _create_answer_prompt(
            item_names, rewritten_query, reranked_docs, histories
        )

        # 2.4 调用模型获取answer
        answer = _call_llm_create_answer(session_id, answer_prompt, is_stream)

        # 2.5 提取片段中的图片image_urls
        image_urls = _extract_chunk_and_url_image(reranked_docs)

        # 2.6 更新state
        state["answer"] = answer
        state["image_urls"] = image_urls

    # 3.保存聊天记录(助手回答)
    _save_assistant_message(state)
    return state


@step_log("_answer_exists_in_state")
def _answer_exists_in_state(state: QueryState) -> bool:
    answer = state.get("answer")
    if answer:
        logger.debug(f"初始节点没有识别到item_name, 已返回answer: {answer}")
        return True
    else:
        logger.debug("未检测到answer, 本次正常生成答案和图片")
        return False


@step_log("_validate_data")
def _validate_data(state: QueryState):
    session_id = state.get("session_id")
    item_names = state.get("item_names")
    rewritten_query = state.get("rewritten_query")
    reranked_docs = state.get("reranked_docs")
    is_stream = state.get("is_stream", False)

    if not session_id or not item_names or not rewritten_query or not reranked_docs:
        logger.error(
            "session_id / item_names / rewritten_query / reranked_docs 参数为空"
        )
        raise ValueError(
            "session_id / item_names / rewritten_query / reranked_docs 参数为空"
        )
    return session_id, item_names, rewritten_query, reranked_docs, is_stream


@step_log("_get_history_by_session_id")
def _get_history_by_session_id(session_id: str) -> list[dict]:
    """获取当前session_id对应的有效聊天记录"""

    histories: list[dict] = get_recent_messages(session_id, limit=QUERY_HISTORY_LIMIT)
    logger.debug(f"查询到聊天记录({session_id}): {len(histories)}")

    histories = [history for history in histories if history.get("item_names")]
    logger.debug(f"查询到有效的聊天记录({session_id}): {len(histories)}")

    return histories


@step_log("_create_answer_prompt")
def _create_answer_prompt(
    item_names: list[str],
    rewritten_query: str,
    reranked_docs: list[dict],
    histories: list[dict],
):
    """拼接提示词"""
    # 参考文档 chunks
    context: str = ""
    for doc in reranked_docs:
        context += (
            f"标题: {doc.get('title')}\n"
            f"来源: {'联网搜索' if doc.get('type') == 'web_search' else '向量数据库'}\n"
            f"置信度: {doc.get('score')}\n"
            f"内容: {doc.get('text')}\n\n"
        )

    # 历史聊天记录 histories
    if histories:
        history_text_list: list[str] = []

        for i, history in enumerate(histories, start=1):
            if history.get("role") == "user":
                history_text_list.append(
                    f"编号: {i} (用户提问)\n"
                    f"原始问题: {history.get('text')},\n"
                    f"改写后的问题: {history.get('rewritten_query')},\n"
                    f"关联的item_names: {','.join(history.get('item_names'))}\n"
                )
            else:
                history_text_list.append(
                    f"编号: {i} (助手回答)\n"
                    f"改写后的问题: {history.get('rewritten_query')}\n"
                    f"回答结果: {history.get('text')[:50]}\n"
                    f"关联的item_names: {','.join(history.get('item_names'))}\n"
                )
        history_text = "\n".join(history_text_list)
    else:
        history_text = "没有有效的历史聊天记录"

    # 主体名称 item_names
    item_names_str: str = ", ".join(item_names)

    answer_prompt = load_prompt(
        name="answer_out",
        context=context,
        history=history_text,
        item_names=item_names_str,
        question=rewritten_query,
    )
    return answer_prompt


@step_log("_call_llm_create_answer")
def _call_llm_create_answer(
    session_id: str,
    answer_prompt: str,
    is_stream: bool,
) -> str:
    """调用模型回答问题"""
    model = infra_model.llm_model()

    if is_stream:
        answer: str = ""
        stream_response = model.stream(answer_prompt)
        for chunk in stream_response:
            chunk_str = chunk.content
            push_to_session(
                session_id=session_id,
                event=SSEEvent.DELTA,
                data={SSEEvent.DELTA: chunk_str},  # 前端约定
            )
            answer += chunk_str
        return answer
    else:
        return model.invoke(answer_prompt).content


@step_log("_extract_chunk_and_url_image")
def _extract_chunk_and_url_image(reranked_docs: list[dict]) -> list[str]:
    """提取chunks中的图片地址"""
    rep = re.compile(r"\!\[.*?\]\((.*?)\)")
    image_urls: list[str] = []

    for chunk in reranked_docs:
        text = chunk.get("text")
        if text:
            image_url_list: list[str] = rep.findall(text)
            if image_url_list:
                image_urls += image_url_list

    logger.info(
        f"chunks中的图片提取完成, 提取数量: {len(image_urls)}\n{'\n'.join(image_urls)}"
    )
    return image_urls


@step_log("_save_assistant_message")
def _save_assistant_message(state: dict):
    """保存模型回答的聊天记录"""
    save_chat_message(
        session_id=state.get("session_id"),
        role="assistant",
        text=state.get("answer"),
        rewritten_query=state.get("rewritten_query"),
        item_names=state.get("item_names", []),
        image_urls=state.get("image_urls", []),
    )
