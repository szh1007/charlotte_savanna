from pathlib import Path

from langchain_core.tools import tool
from rich import print as rprint

from ..api.context import get_session_context
from ..api.monitor import monitor
from ..utils.path_utils import resolve_path


@tool
def generate_markdown(content: str, filename: str, path: str = "") -> str:
    """
    根据提供的文本内容, 生成对应的 Markdown(.md) 文件
    自动补全 .md 后缀, 支持相对路径或绝对路径保存

    Args:
        content: 要写入 Markdown 文档的文本内容
        filename: Markdown 文档的文件名(不包含扩展名或包含 .md)
        path: 文件保存的路径(相对会话目录或绝对路径), 默认为空

    Returns:
        生成结果信息; 失败时返回错误信息
    """
    rprint(f"generate_markdown - path: {path}")
    monitor.report_tool("Markdown文档生成工具", {"content": content})

    # ====================== 1. 路径清洗与重定向逻辑 ======================
    # 自动补全 .md 后缀
    if not filename.endswith(".md"):
        filename += ".md"

    # 获取上下文中的会话目录
    session_dir = get_session_context()
    rprint(f"⚠️ generate_markdown - session_dir: {session_dir}")

    # 结合 path 和 filename 拼接完整路径
    full_input_path = str(Path(path) / filename) if path and path != "." else filename

    # 使用 Path 拼接, 再转为字符串传给 resolve_path
    full_path_str = resolve_path(full_input_path, session_dir)
    file_path = Path(full_path_str)

    # 获取父目录
    parent_dir = file_path.parent

    rprint(
        "[MarkdownTool] Debug: "
        f"parent_dir={parent_dir}, "
        f"filename={filename}, full_path={file_path}"
    )

    try:
        # 确保目录存在
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, exist_ok=True)
            rprint(f"[MarkdownTool] Created directory: {parent_dir}")

        # 使用 Path 直接写入文本
        file_path.write_text(content, encoding="utf-8")

        rprint(f"[MarkdownTool] Successfully wrote to: {file_path}")
        return f"Markdown 文件 '{file_path}' 已成功生成并保存。"
    except Exception as e:
        rprint(f"[MarkdownTool] Error writing file: {e!s}")
        return f"[ERROR] 生成 Markdown 文件失败: {e!s}"


# ====================== 测试入口 ======================

if __name__ == "__main__":
    # 1. 固定 session_dir (仅赋值, 不Mock)
    def get_session_context():
        return "./test_session_123"

    # 2. 定义测试参数
    test_content = "# 测试文档\n这是给session_dir配置固定值后的测试内容"
    test_filename = "测试文件"  # 无.md后缀, 测试自动补全
    test_path = "sub_dir"  # 相对路径

    # 3. 测试调用 (仅传 path/filename, session_dir 已初始化)
    result = generate_markdown.invoke(
        {"content": test_content, "filename": test_filename, "path": test_path}
    )
    print("===== 调用结果 =====")
    print(result)

    # 4. 验证结果
    if "已成功生成" in result:
        file_path = Path(result.split("'")[1])
        print(f"✅ 验证: 文件 {file_path} {'存在' if file_path.exists() else '不存在'}")
