import os
from pathlib import Path

import dotenv
from ragflow_sdk import RAGFlow
from rich import print as rprint

dotenv.load_dotenv()


def create_data_set():
    """RAGFlow - 创建知识库"""

    ragflow = RAGFlow(
        base_url=os.getenv("DS_RAGFLOW_API_URL", ""),
        api_key=os.getenv("DS_RAGFLOW_API_KEY", ""),
    )

    created_dataset = ragflow.create_dataset(
        name="测试知识库", embedding_model="text-embedding-v3@Tongyi-Qianwen"
    )
    rprint(created_dataset)


def upload_file_to_data_set():
    """RAGFlow - 上传文件到知识库"""

    ragflow = RAGFlow(
        base_url=os.getenv("DS_RAGFLOW_API_URL", ""),
        api_key=os.getenv("DS_RAGFLOW_API_KEY", ""),
    )

    current_dataset = ragflow.list_datasets(id="your-dataset-id")[0]

    document_list = []
    xlsx_path = Path() / "test.xlsx"
    document_list.append(
        {
            "display_name": xlsx_path.stem,  # 展示名称 (stem 无后缀 / name 有后缀)
            "name": xlsx_path.stem,  # 文件名
            "blob": xlsx_path.read_bytes(),  # 文件二进制内容
        }
    )
    rprint(current_dataset.upload_documents(document_list))


if __name__ == "__main__":
    create_data_set()
    upload_file_to_data_set()
