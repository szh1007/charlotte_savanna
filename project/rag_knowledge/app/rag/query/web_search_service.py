from tavily import TavilyClient

from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import logger, step_log


@step_log("search_by_web")
def search_by_web(state: QueryState) -> QueryState:
    # 1.检验并获取数据
    rewritten_query = _validate_data(state)

    # 2.调用 tavily 搜索引擎
    web_search_docs = _call_tavily_search(rewritten_query)

    return web_search_docs


@step_log("_validate_data")
def _validate_data(state: QueryState):
    rewritten_query = state.get("rewritten_query")
    if not rewritten_query:
        logger.error("rewritten_query 参数为空")
        raise ValueError("rewritten_query 参数为空!")
    return rewritten_query


@step_log("_call_tavily_search")
def _call_tavily_search(rewritten_query: str):
    """调用 tavily 搜索引擎"""
    tavily = TavilyClient()
    search_result = tavily.search(rewritten_query, max_results=10)
    results = search_result.get("results", [])

    web_search_docs = []
    for result in results:
        if result.get("score") > 0.5:
            web_search_docs.append(result)

    return web_search_docs


# @step_log("_call_mcp_tool")
# async def _call_mcp_tool(rewritten_query: str):
#     """OpenAI MCP"""
#     mcp_server = MCPServerStreamableHttp(
#         name="TEST_MCP_NAME",
#         params={
#             "url": "TEST_MCP_URL",
#             "headers": {"Authorization": f"Bearer TEST_API_KEY"},
#             "timeout": 10,
#         },
#         cache_tools_list=True,
#         max_retry_attempts=3,
#     )

#     # 连接mcp服务
#     await mcp_server.connect()

#     try:
#         # list_tool = await mcp_server.list_tools()
#         result = await mcp_server.call_tool(
#             tool_name="TEST_TOOL_NAME",
#             arguments={"query": rewritten_query, "count": 10},
#         )
#         return result
#     except Exception as e:
#         logger.exception(f"MCP连接失败/调用工具失败: {str(e)}")
#     finally:
#         # 释放mcp资源实例
#         await mcp_server.cleanup()
