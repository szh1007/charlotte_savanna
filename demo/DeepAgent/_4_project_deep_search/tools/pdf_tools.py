import logging
from pathlib import Path

from langchain_core.tools import tool

from ..api.context import get_session_context
from ..api.monitor import monitor
from ..utils.path_utils import resolve_path
from ..utils.word_converter import convert_md_to_pdf_via_word


@tool
def convert_md_to_pdf(md_filename: str, pdf_filename: str | None = None) -> str:
    """
    将 Markdown 文档转换为 PDF(基于 Word 引擎)
    核心优化: 路径与资源管理逻辑分离, 只保留 Tool 层的基础调用

    Args:
        md_filename: 要转换的 Markdown 文档路径(包含 .md 后缀)
        pdf_filename: 输出的 PDF 文件路径(可选, 默认与源文件同名)

    Returns:
        转换结果信息; 失败时返回错误信息
    """
    monitor.report_tool(
        "Markdown转PDF工具", {"md_filename": md_filename, "pdf_filename": pdf_filename}
    )

    try:
        # ====================== 1. 路径预处理 ======================
        session_dir = get_session_context()
        md_path = Path(md_filename).with_suffix(".md")
        md_abs_path = Path(resolve_path(str(md_path), session_dir))

        # ====================== 2. 检查源文件 ======================
        if not md_abs_path.exists():
            return f"[ERROR] 文件不存在: {md_abs_path}"

        # ====================== 3. 确定输出路径 ======================
        if pdf_filename:
            pdf_path = Path(pdf_filename).with_suffix(".pdf")
            pdf_abs_path = Path(resolve_path(str(pdf_path), session_dir))
        else:
            pdf_abs_path = md_abs_path.with_suffix(".pdf")

        # ====================== 4. 调用核心转换逻辑 ======================
        return convert_md_to_pdf_via_word(md_abs_path, pdf_abs_path)

    except Exception as e:
        logging.error(f"转换失败: {e}", exc_info=True)
        return f"[ERROR] 转换失败: {e!s}"


# ====================== 测试入口 ======================

if __name__ == "__main__":
    # 1. 固定 session_dir (仅赋值, 不Mock)
    def get_session_context():
        return "./test_session_123"

    # 2. 创建测试文件
    test_dir = Path("./test_session_123/sub_dir")
    test_dir.mkdir(parents=True, exist_ok=True)
    test_file = test_dir / "测试文件.md"
    test_file.write_text(
        "# 标题\n\n测试内容\n\n|A|B|\n|---|---|\n|1|2|", encoding="utf-8"
    )

    # 3. 测试调用
    result = convert_md_to_pdf.invoke({"md_filename": "sub_dir/测试文件.md"})
    print("===== 调用结果 =====")
    print(result)
