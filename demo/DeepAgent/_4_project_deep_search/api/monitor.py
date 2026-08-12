import asyncio
import contextlib
import datetime
from typing import Any

from .context import get_thread_context

# 尝试导入全局运行时(用于脚本模式下的流式输出)
try:
    import builtins
except ImportError:
    builtins = None


class ToolMonitor:
    """
    工具监控类, 用于在工具执行过程中上报进度和状态.
    设计为单例模式, 可在任何工具中直接导入使用.
    兼容 FastAPI WebSocket 和 脚本运行时的 stream_writer.

    使用示例:
    from api.monitor import monitor

    def my_tool(arg1):
        monitor.report_start("my_tool", {"arg1": arg1})
        ...
        monitor.report_running("my_tool", "正在处理数据...", progress=0.5)
        ...
        monitor.report_end("my_tool", result)
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.websocket_manager = None  # 预留给 FastAPI WebSocketManager
        return cls._instance

    def set_websocket_manager(self, manager):
        """设置 FastAPI 的 WebSocket 管理器"""
        self.websocket_manager = manager

    def _emit(self, event_type: str, message: str, data: dict[str, Any] | None = None):
        """内部发送方法"""
        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": message,
            "data": data or {},
            "timestamp": datetime.datetime.now().isoformat(),
        }

        # 1. 优先尝试通过 FastAPI WebSocket 发送 (定向推送)
        if self.websocket_manager:
            try:
                # 获取当前线程 ID
                """
                websocket-4
                    每次工具调用时, 都会根据 ContextVar 获取当前 thread_id
                """
                thread_id = get_thread_context()

                """
                loop-3
                    如果当前的方法要使用 websocket 发送信息,
                    只有当前的 loop 等于一开始绑定到 manager 的 loop,
                    也就是等于 websocket 所属的 loop, 这个方法才能使用 websocket.
                    但是这里直接统一写成, 只要 manager 有 loop 属性,
                    无论怎样, 强制将要使用 websocket 的方法转移到该 loop, 就能发送了.

                    (本质就是将要使用 websocket 的方法,
                    从当前 loop 转移到 websocket 所属 loop)
                """
                # 假设 manager 有 loop 属性指向创建它的事件循环
                if (
                    hasattr(self.websocket_manager, "loop")
                    and self.websocket_manager.loop
                ):
                    if thread_id:
                        """
                        websocket-5-1
                            要根据 thread_id 发送消息到对应的 WebSocket
                        """
                        asyncio.run_coroutine_threadsafe(
                            self.websocket_manager.send_to_thread(payload, thread_id),
                            self.websocket_manager.loop,
                        )
                    else:
                        # 如果没有 thread_id, 说明可能是系统级消息, 或者未上下文环境
                        # 可以选择广播, 或者忽略. 这里选择仅在控制台输出警告
                        pass
            except Exception as e:
                print(f"[Monitor] WebSocket send failed: {e}")

        # 2. 尝试通过全局 runtime 输出 (DeepAgents 脚本模式)
        # 这使得 simple_agents.py 中的 MockRuntime 能接收到数据
        if (
            builtins
            and hasattr(builtins, "runtime")
            and hasattr(builtins.runtime, "stream_writer")
        ):
            with contextlib.suppress(Exception):
                builtins.runtime.stream_writer(payload)

        # 3. 控制台保底输出 (方便调试)
        # 加上特殊前缀, 方便肉眼识别
        print(f"\n[Monitor: {event_type}] {message}")

    def report_tool(self, tool_name: str, args: dict[str, Any] | None = None):
        """报告工具开始执行"""
        self._emit(
            "tool_start",
            f"开始执行工具: {tool_name}",
            {"tool_name": tool_name, "args": args},
        )

    def report_assistant(self, assistant_name: str, args: dict[str, Any] | None = None):
        """报告正在调用的子智能体进度"""
        self._emit(
            "assistant_call",
            f"正在调用助手: {assistant_name}",
            {"assistant_name": assistant_name, "args": args},
        )

    def report_task_result(self, result: str):
        """报告任务最终结果"""
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_session_dir(self, path: str):
        """报告任务工作目录"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})


# 全局单例实例
monitor = ToolMonitor()
