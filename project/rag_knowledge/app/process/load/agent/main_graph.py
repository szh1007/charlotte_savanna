from langgraph.graph import END, StateGraph

from ..nodes._01_entry import node_entry
from ..nodes._02_pdf_to_md import node_pdf_to_md
from ..nodes._03_md_img import node_md_img
from ..nodes._04_document_split import node_document_split
from ..nodes._05_item_name_recognition import node_item_name_recognition
from ..nodes._06_bge_embedding import node_bge_embedding
from ..nodes._07_import_milvus import node_import_milvus
from .state import LoadState


def router_after_entry(state: LoadState) -> str:
    """路由函数"""
    if state["is_md_read_enabled"]:
        return "Markdown"
    elif state["is_pdf_read_enabled"]:
        return "PDF"
    else:
        return "文件格式不支持"


graph = (
    StateGraph(state_schema=LoadState)
    .add_node(node_entry)  # 入口节点
    .add_node(node_pdf_to_md)  # PDF 转 Markdown 节点
    .add_node(node_md_img)  # 处理 Markdown 中的图片资源节点
    .add_node(node_document_split)  # 文档分块节点
    .add_node(node_item_name_recognition)  # 项目名称识别节点
    .add_node(node_bge_embedding)  # BGE 嵌入节点
    .add_node(node_import_milvus)  # 导入向量库节点
    .set_entry_point("node_entry")
    .add_conditional_edges(
        "node_entry",
        router_after_entry,
        path_map={
            "Markdown": "node_md_img",
            "PDF": "node_pdf_to_md",
            "文件格式不支持": END,
        },
    )
    .add_edge("node_pdf_to_md", "node_md_img")
    .add_edge("node_md_img", "node_document_split")
    .add_edge("node_document_split", "node_item_name_recognition")
    .add_edge("node_item_name_recognition", "node_bge_embedding")
    .add_edge("node_bge_embedding", "node_import_milvus")
    .add_edge("node_import_milvus", END)
).compile()
