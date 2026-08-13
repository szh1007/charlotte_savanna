import shutil
from pathlib import Path


def prepare_session_environment(thread_id: str) -> tuple[str, str, str]:
    """
    初始化会话运行环境(会话文件夹, 相对路径, 上传文件信息).

    目标:
    1. 创建独立的物理工作空间.
    2. 处理用户上传的文件.
    3. 生成供 Agent 和前端使用的路径上下文(提示词).

    执行步骤:
    1. 创建绝对路径: `project_root/output/session_{uuid}`.
    2. 标准化路径: 转换为 POSIX 风格 (`/`) 以兼容 LLM 和跨平台.
    3. 文件迁移: 将 `updated/session_{uuid}` 中的文件复制到工作目录.
    4. 构造提示词: 生成包含已上传文件列表的 Context 文本.

    Args:
        thread_id: 会话 ID, 用于定位当前会话的工作目录.

    Returns:
        session_dir_str (str): 物理工作目录的绝对路径(当前会话对应文件存储位置, 给前端).
        relative_session_dir (str): 相对于项目根目录的路径(用于提示词, 给模型看).
        uploaded_info (str): 注入到 Prompt 中的文件列表描述.
    """
    # ====================== 1. 创建会话绝对输出路径 ======================
    # 项目根目录/output/session_{thread_id}
    project_root = Path(__file__).parents[1].resolve()
    session_dir = project_root / "output" / f"session_{thread_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # ====================== 2. 标准化为 POSIX 路径 ======================
    # 反斜杠转正斜杠, 防止大模型对 Windows 路径产生幻觉
    session_dir_str = str(session_dir).replace("\\", "/")

    # ====================== 3. 获取相对路径 ======================
    # 相对项目根的地址(如 "output/session_123"), 供提示词使用
    relative_session_dir = str(session_dir.relative_to(project_root)).replace("\\", "/")

    # ====================== 4. 迁移上传文件 ======================
    # upload_dir 为上传临时区, session_dir 为正式工作区
    # 转存后再对外展示, 用于区分用户上传与本次生成的文件
    upload_dir = project_root / "updated" / f"session_{thread_id}"
    uploaded_info = ""

    if upload_dir.exists():
        files = [f.name for f in upload_dir.iterdir() if f.is_file()]

        if files:
            # 将文件从临时上传区复制到正式工作区
            for f in files:
                shutil.copy2(upload_dir / f, session_dir / f)

            # ====================== 5. 构造文件列表提示词 ======================
            uploaded_info = (
                "\n    [已上传文件] 已加载到工作目录:\n"
                + "\n".join([f"    - {f}" for f in files])
                + "\n    请优先使用工具读取并参考这些文件."
            )

    return session_dir_str, relative_session_dir, uploaded_info
