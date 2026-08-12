from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver

from ..api.context import reset_session_context, set_session_context, set_thread_context
from ..api.monitor import monitor
from ..tools.markdown_tools import generate_markdown
from ..tools.pdf_tools import convert_md_to_pdf
from ..tools.upload_file_read_tool import read_file_content
from ..utils.session import prepare_session_environment
from ..utils.stream import process_stream_chunk
from .llm import model
from .prompt import main_agent_config
from .subagents.database_query_agent import database_query_agent
from .subagents.network_search_agent import network_search_agent

# from subagents.kownledge_base_agent import kownledge_base_agent


main_agent = create_deep_agent(
    model=model,
    system_prompt=main_agent_config["system_prompt"],
    tools=[
        read_file_content,  # 读取文件内容
        generate_markdown,  # 生成 markdown
        convert_md_to_pdf,  # 转换 markdown 为 pdf
    ],
    subagents=[
        network_search_agent,  # 联网搜索
        database_query_agent,  # 数据库搜索
        # kownledge_base_agent,  # 知识库搜索
    ],
    checkpointer=InMemorySaver(),
    store=None,
    backend=None,
    permissions=None,
    interrupt_on=None,
    memory=None,  # AGENTS.md, preferences.md
    skills=None,
    context_schema=None,
)


async def run_deep_agent(task_query: str, thread_id: str | None = None):
    """
    DeepAgents 核心执行入口 (Agent Execution Runtime).

    目标:
    1. 接收用户的自然语言任务.
    2. 准备独立的运行环境 (Workspace).
    3. 启动 LangGraph 智能体, 并通过流式 (Stream) 实时处理每一步.
    4. 确保上下文隔离和异常安全.

    执行步骤:
    1. ID 初始化: 确保每个任务有唯一的 `thread_id`.
    2. 环境准备: 创建目录, 迁移文件, 生成路径信息.
    3. 上下文绑定: 将 `thread_id` 和 `session_dir` 绑定到当前线程 (ContextVar).
    4. 提示词构建: 将环境信息注入到 Prompt.
    5. 流式执行: 驱动 LangGraph 运行, 并实时解析/上报每一个 Chunk.
    6. 资源清理: 任务结束后 (无论成功失败) 重置上下文.
    """
    # ====================== 1. 环境准备: 创建目录, 处理上传文件 ======================
    # session_dir_str -> 真实地址 -> 给前端
    # relative_session_dir -> 相对地址 -> 给模型看 -> 放在提示词中
    # uploaded_info -> 上传了哪些文件告诉模型, 让模型去读
    session_dir_str, relative_session_dir, uploaded_info = prepare_session_environment(
        thread_id
    )

    # ====================== 2. 上下文绑定: 初始化 ContextVars (关键: 隔离并发请求) ======================  # noqa: E501
    # 协程共享同一个线程id
    thread_token = set_thread_context(thread_id)

    session_token = set_session_context(session_dir_str)

    # 给前端推送文件夹, 方便后续查询当前会话对应文件夹下的所有文件
    # 给前端传递真实存储数据的文件夹
    monitor.report_session_dir(session_dir_str)

    # ====================== 3. 运行时配置: LangChain Config (注入记忆 key) ======================  # noqa: E501
    config = {
        "configurable": {"thread_id": thread_id},  # 用于 MemorySaver 记忆上下文
    }
    # ====================== 4. 提示词构建: 动态注入环境约束 ======================
    path_instruction = f"""
    [工作环境指令]
    工作目录: {relative_session_dir}
    {uploaded_info}

    规则:
    1. 新生成文件必须保存到工作目录: '{relative_session_dir}/filename'
    2. 读取已上传的文件时, 请直接将文件名(例如: '开篇.txt')作为 filename 参数传入
        (read_file_content)读取工具, 不要带上任何目录前缀.
    3. 使用相对路径, 禁止使用绝对路径
    4. 若存在上传文件, 请先分析内容
    """

    # ====================== 5. 流式执行: 启动 Agent 循环 ======================
    try:
        # astream: 异步生成器, 像流水线一样逐个吐出 Agent 的思考片段
        async for chunk in main_agent.astream(
            {"messages": [{"role": "user", "content": task_query + path_instruction}]},
            config=config,
        ):
            # 实时处理每一个片段 (上报前端)
            process_stream_chunk(chunk)
        return "Done"
    except Exception as e:
        # ====================== 6. 异常处理: 兜底捕获 ======================
        print(f"Error: {e}")
        monitor._emit("error", f"Execution failed: {e}")
        return f"Error: {e}"

    finally:
        # ====================== 7. 资源清理: 必须重置 ContextVars, 防止线程池复用导致的上下文污染 ======================  # noqa: E501
        if "session_token" in locals():
            reset_session_context(session_token, thread_token)
