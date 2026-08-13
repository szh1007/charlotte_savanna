from ..api.monitor import monitor


def process_stream_chunk(chunk: dict) -> None:
    """
    处理 LangGraph 流式输出的增量状态(Stream Processing).

    目标:
    1. 解析 Agent 的每一步思考和行动.
    2. 识别关键事件(工具调用, 子 Agent 委派, 最终回复).
    3. 通过 Monitor 实时上报状态给前端.

    核心逻辑:
    - 监听 `tool_calls`: 若是 'task', 上报子 Agent 状态.
    - 监听 `content`: 若无工具调用, 视为 Agent 的最终回复.

    Args:
        chunk (dict): 增量状态字典, 如 {"node_name": {"messages": [AIMessage(...)]}}
    """
    # ====================== 1. 记录原始数据便于回溯 ======================
    # logger.log_main_chunk(chunk)

    # ====================== 2. 遍历解析每个节点的输出 ======================
    # 通常为 'agent' 或 'tools' 节点, 结构如 {"model/tools": {"messages": [AI / Tool]}}
    for node_name, state in chunk.items():
        if not state or "messages" not in state:
            continue

        # ====================== 3. 提取最新一条消息 ======================
        messages = state["messages"]
        if isinstance(messages, list) and messages:
            last_msg = messages[-1]

            # ====================== 4. 分支处理 AI 消息 (AIMessage) ======================  # noqa: E501
            if node_name == "model":
                # Case 1: Agent 决定调用工具 (Tool Call)
                if last_msg.tool_calls:
                    for tool in last_msg.tool_calls:
                        # 若为 'task' 工具, 说明正在委派子 Agent, 向前端上报
                        if tool["name"] == "task":
                            monitor.report_assistant(
                                tool["args"].get("subagent_type", "Agent"),
                                {"desc": tool["args"].get("description")},
                            )
                        # 非 task 工具调用的监控埋点, 下移到各工具内部

                # Case 2: Agent 生成最终回复 (Final Answer)
                elif last_msg.content:
                    monitor.report_task_result(last_msg.content)
