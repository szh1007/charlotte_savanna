import shutil
import uuid
from datetime import datetime
from mimetypes import guess_type
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from ...process.load.agent.main_graph import graph
from ...process.load.agent.state import create_default_state
from ...shared.runtime.logger import PROJECT_ROOT, logger
from ...shared.utils.task_utils import (
    get_done_task_list,
    get_running_task_list,
    get_task_status,
    update_task_status,
)
from .schema import UploadResponse, UploadStatusResponse

app = FastAPI()


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/upload/frontend")


@app.get("/upload/frontend", response_class=FileResponse)
async def upload_frontend():
    html_path_obj: Path = Path(__file__).parent.parent / "html" / "upload.html"
    return FileResponse(
        path=html_path_obj,
        media_type=guess_type(html_path_obj.name)[0],
    )


def resolve_upload_file(task_id: str, local_file_path: str):
    state = create_default_state(
        task_id=task_id,
        local_file_path=local_file_path,
    )
    logger.info("------------------整体开始执行解析------------------\n")

    try:
        update_task_status(task_id, "processing")
        state = graph.invoke(state)
        update_task_status(task_id, "completed")
    except Exception:
        update_task_status(task_id, "failed")

    logger.info("------------------整体执行解析结束------------------\n")


@app.post("/upload", response_model=UploadResponse)
async def upload(
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
):
    task_ids: list[str] = []
    for file in files:
        # 当前时间戳
        now_day: datetime = datetime.now().strftime("%Y%m%d")
        now_time: datetime = datetime.now().strftime("%Y%m%d%H%M%S")

        # 拼接文件名
        tar_name = f"{now_time}_{file.filename}"

        # 拼接文件路径
        local_file_dir_obj = Path(PROJECT_ROOT) / "assets" / now_day
        local_file_dir_obj.mkdir(parents=True, exist_ok=True)
        local_file_path_obj = local_file_dir_obj / tar_name
        local_file_path = str(local_file_path_obj)

        # 文件写入 (分块写入)
        with local_file_path_obj.open("wb") as file_buffer:
            shutil.copyfileobj(file.file, file_buffer)

        # 生成task_id
        current_task_id = str(uuid.uuid4())
        task_ids.append(current_task_id)

        # 开启异步解析任务
        background_tasks.add_task(
            func=resolve_upload_file,
            task_id=current_task_id,
            local_file_path=local_file_path,
        )

    return UploadResponse(
        message=f"文件上传成功: {local_file_path}",
        task_ids=task_ids,
    )


@app.get("/upload/status/{task_id}", response_model=UploadStatusResponse)
async def upload_status(task_id: str):
    return UploadStatusResponse(
        task_id=task_id,
        status=get_task_status(task_id),
        done_list=get_done_task_list(task_id),
        running_list=get_running_task_list(task_id),
    )


if __name__ == "__main__":
    uvicorn.run(
        app="project.rag_knowledge.app.api.server.upload:app",
        host="127.0.0.1",
        port=8100,
        reload=True,
    )
