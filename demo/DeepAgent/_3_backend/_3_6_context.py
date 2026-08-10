"""
Input Context
输入上下文 - 启动时注入: tool_prompt, system_prompt, memory, skills
    tool_prompt: 解释工具的使用、作用、参数、返回值
    system_prompt
        角色: ...
        能力: ...
        边界: ...
    memory: 定义长期规则和用户偏好, 本质是加载外部文件
        memory = ["/memory/AGENTS.md", "/memory/preferences.md"]
    skills: 解决指定方案 (渐进式加载)
        skills = ["/skills"]

Runtime Context
运行时上下文 - 每次执行会动态改变
tool / subagent 可以接收主agent的Context
    @dataclass - invoke(..., Context=...)

Isolated Context
子代理隔离 - 每个子代理的上下文是互相隔离, 不会干扰的

Long/Short-term Memory
被动 - 长短期记忆
    store = InMemoryStore()
    checkpointer = InMemorySaver()
主动 - 长期记忆
    backend = FilesystemBackend(workspace_dir, virtual_mode=True)
"""
