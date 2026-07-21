import dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    FilesystemFileSearchMiddleware,
    LLMToolEmulator,
    LLMToolSelectorMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

dotenv.load_dotenv()

model = init_chat_model("deepseek:deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}})

# 仅做定义上的展示
# 中间件顺序: 非常重要!!!
agent = create_agent(
    model=model,
    tools=[],
    checkpointer=InMemorySaver(),
    middleware=[
        ModelCallLimitMiddleware(  # 模型限制
            thread_limit=2,  # 会话限制 - 每个线程最多调用2次
            run_limit=5,  # 单次调用限制 - 最多调用5次
            exit_behavior="end",  # "end" 优雅退出 / "error" 抛异常
        ),
        ToolCallLimitMiddleware(  # 工具限制
            thread_limit=2,  # 会话限制 - 每个线程最多调用2次
            run_limit=5,  # 单次调用限制 - 最多调用5次
            exit_behavior="end",  # "end" 优雅退出 / "error" 抛异常
        ),
        ModelFallbackMiddleware(  # 高可用, 主模型异常, 启用备用模型
            first_model="deepseek:deepseek-v4-pro",
            additional_models="deepseek:deepseek-v4-flash",
        ),
        LLMToolSelectorMiddleware(  # 智能工具筛选
            model="deepseek:deepseek-v4-flash",  # 子模型
            max_tools=5,  # 最多5个工具
            always_include=["tavily_search"],  # 总是使用
        ),
        ToolRetryMiddleware(  # 工具重试策略 (指数退避策略)
            max_retries=3,  # 最多重试3次
            backoff_factor=2,  # 指数退避因子
            initial_delay=1,  # 初始延迟时间
            max_delay=60,  # 最大延迟时间
            jitter=True,  # 开启随机延迟 (建议开启)
            retry_on=[],  # 仅在xx时重试
            on_failure="continue",  # 最大重试次数后依然失败后采用的措施, 继续执行
        ),
        ModelRetryMiddleware(  # 模型重试策略 (指数退避策略)
            max_retries=3,  # 最多重试3次
            backoff_factor=2,  # 指数退避因子
            initial_delay=1,  # 初始延迟时间
            max_delay=60,  # 最大延迟时间
            jitter=True,  # 开启随机延迟 (建议开启)
            retry_on=[],  # 仅在xx时重试
            on_failure="continue",  # 最大重试次数后依然失败后采用的措施, 继续执行
        ),
        LLMToolEmulator(  # 模型工具
            model="deepseek:deepseek-v4-flash",  # 子模型
        ),
        ContextEditingMiddleware(  # 上下文管理
            edits=[
                ClearToolUsesEdit(  # 配置 - 清除工具调用记录
                    trigger=1024,  # 触发编辑的token数
                    keep=0,  # 0 工具全部清除
                ),
            ]
        ),
        FilesystemFileSearchMiddleware(  # 本地文件搜索和分析 Glob / Grep
            root_path="../",  # 检索根目录
            allowed_extensions=[".py", ".md"],  # 允许的文件扩展名
            use_ripgrep=True,  # 是否使用 ripgrep
            max_file_size_mb=10,  # 最大文件大小 (MB)
        ),
    ],
)
