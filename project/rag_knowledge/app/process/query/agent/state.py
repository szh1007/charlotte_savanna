import copy
from typing import TypedDict

from pydantic import Field


class QueryState(TypedDict):
    session_id: str = Field(..., description="会话ID")
    original_query: str = Field(..., description="用户原始问题")

    # 检索过程中的中间数据
    embedding_chunks: list  # 普通向量检索回来的切片
    hyde_embedding_chunks: list  # HyDE 检索回来的切片
    web_search_docs: list  # 网络搜索回来的文档

    # 排序过程中的数据
    rrf_chunks: list  # RRF 融合排序后的切片
    reranked_docs: list  # 重排序后的最终 Top-K 文档

    # 生成过程中的数据
    prompt: str  # 组装好的 Prompt
    answer: str  # 最终生成的答案

    # 辅助信息
    item_names: list[str]  # 提取出的商品名称
    rewritten_query: str  # 改写后的问题
    history: list  # 历史对话记录
    is_stream: bool  # 是否流式输出标记
    image_urls: list[str]  # 答案中引用的图片链接


query_default_state: QueryState = {
    "session_id": "",
    "original_query": "",
    "embedding_chunks": [],
    "hyde_embedding_chunks": [],
    "web_search_docs": [],
    "rrf_chunks": [],
    "reranked_docs": [],
    "prompt": "",
    "answer": "",
    "item_names": [],
    "rewritten_query": "",
    "history": [],
    "is_stream": False,
    "image_urls": [],
}


def create_query_default_state(**kwargs) -> QueryState:
    """获取默认状态 - 可以使用部分参数初始化"""
    state = copy.deepcopy(query_default_state)
    state.update(kwargs)
    return state


if __name__ == "__main__":
    print(
        create_query_default_state(
            session_id="123",
            original_query="华为mate40手机的产品功能如何",
            is_stream=False,
        )
    )
    print(create_query_default_state())
