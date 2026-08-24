import copy
from typing import TypedDict

from pydantic import Field


class LoadState(TypedDict):
    # 跟踪加载任务
    task_id: str = Field(..., description="加载任务ID")

    # 待加载的文件 - 确认前
    local_file_path: str = Field(..., description="本地文件路径")

    # 待加载的文件 - 确认后
    md_path: str = Field(..., description="Markdown文件路径")
    pdf_path: str = Field(..., description="PDF文件路径")
    local_dir: str = Field(..., description="本地目录路径")
    file_title: str = Field(..., description="文件标题")

    # 文件状态
    is_md_read_enabled: bool = Field(..., description="Markdown是否可读取")
    is_pdf_read_enabled: bool = Field(..., description="PDF是否可读取")

    # 文件内容
    md_content: str = Field(..., description="Markdown文件内容")

    # 分块
    item_name: str = Field(..., description="主体名称")
    chunks: list[dict] = Field(..., description="分块列表")
    embeddings: list[dict] = Field(..., description="分块向量列表")


# 模板对象
graph_default_state: LoadState = {
    "task_id": "",
    "local_file_path": "",
    "md_path": "",
    "pdf_path": "",
    "local_dir": "",
    "file_title": "",
    "is_md_read_enabled": False,
    "is_pdf_read_enabled": False,
    "md_content": "",
    "item_name": "",
    "chunks": [],
    "embeddings": [],
}


def create_default_state(**kwargs) -> LoadState:
    """获取默认状态 - 可以使用部分参数初始化"""
    copy_state = copy.deepcopy(graph_default_state)
    copy_state.update(kwargs)
    return copy_state


if __name__ == "__main__":
    """
    json
        dump    [倾倒]  dict - json文件中(json字符串)
        dumps           dict - json字符串
        load    [加载]  json文件(json字符串) -> dict
        loads           json字符串 -> dict
    """
    print(create_default_state(task_id="123"))
    print(create_default_state())
