import os
from pathlib import Path


def resolve_path(filename: str, session_dir: str | None = None) -> str:
    """
    统一的文件路径解析工具方法.

    核心功能:
    1. 清洗虚拟路径前缀 (/workspace, /mnt/data, /home/user)
    2. 识别 updated/ 目录, 优先相对于项目根目录解析
    3. 结合 session_dir 处理相对/绝对路径, 保证路径隔离
    4. 防止路径嵌套 (session_id/session_id)

    Args:
        filename (str): 输入的文件名或路径
        session_dir (str, optional): 会话上下文目录

    Returns:
        str: 解析后的绝对路径
    """
    path = Path(filename)
    path_str = filename.replace("\\", "/")  # 统一处理字符串匹配

    # 1. 虚拟路径清洗
    virtual_prefixes = ["/workspace", "/mnt/data", "/home/user"]
    for prefix in virtual_prefixes:
        if path_str.startswith(prefix):
            # 去掉前缀
            cleaned = path_str[len(prefix) :].lstrip("/")
            path = Path(cleaned)
            path_str = str(path).replace("\\", "/")
            break

    # 2. 特殊处理: updated/ (用户上传文件)
    # 只要路径中包含 updated/, 就提取其后半部分, 并相对于 CWD 解析
    if "updated/" in path_str:
        idx = path_str.find("updated/")
        relative_part = path_str[idx:]
        return str(Path(relative_part).resolve())

    if not session_dir:
        return str(path.resolve())

    session_path = Path(session_dir).resolve()
    session_name = session_path.name

    # 3. 结合 Session Context

    # 检测 Unix 风格绝对路径 (以 / 开头)
    is_unix_abs = path_str.startswith("/")

    # 如果是绝对路径 (Windows带盘符 或 Unix/开头)
    if path.is_absolute() or (os.name == "nt" and is_unix_abs):
        # Windows 特殊情况: 以 / 开头但无盘符, 视为相对路径
        if os.name == "nt" and is_unix_abs and not path.drive:
            full_path = session_path / path_str.lstrip("/")
        else:
            full_path = path.resolve()

        # 检查是否在 session 目录内
        try:
            # 判断 full_path 是否是 session_path 的子路径
            if session_path in full_path.parents or full_path == session_path:
                # 检查嵌套 (例如 .../session_abc/session_abc/file.txt)
                # 检查路径部分中是否有连续重复的 session_name
                parts = full_path.parts
                for i in range(len(parts) - 1):
                    if parts[i] == session_name and parts[i + 1] == session_name:
                        # 发现嵌套, 修正为 session_dir / filename
                        return str(session_path / full_path.name)
                return str(full_path)
        except Exception:
            pass

        # 绝对路径但不在 session_dir 下 -> 保持原样
        return str(full_path)

    else:
        # 相对路径处理
        parts = path.parts

        # 检查是否包含 session_name (避免重复) 或 output/ 前缀
        if session_name in parts:
            return str(session_path / path.name)

        if parts and parts[0] == "output":
            return str(session_path / path.name)

        # 默认: 拼接到 session_dir
        return str(session_path / path)
