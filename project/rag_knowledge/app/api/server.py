import shutil
import uuid
from datetime import datetime
from mimetypes import guess_type
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, UploadFile
from fastapi.requests import Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from ..process.load.agent.main_graph import graph as load_graph
from ..process.load.agent.state import create_default_state
from ..process.query.agent.main_graph import graph as query_graph
from ..process.query.agent.state import create_query_default_state
from ..shared.runtime.logger import PROJECT_ROOT, logger
from ..shared.utils.sse_utils import (
    SSEEvent,
    create_sse_queue,
    push_to_session,
    sse_generator,
)
from ..shared.utils.task_utils import (
    clear_task,
    get_done_task_list,
    get_running_task_list,
    get_task_status,
    update_task_status,
)
from .schema import (
    QueryAsyncResponse,
    QueryRequest,
    QuerySyncResponse,
    TaskStatusResponse,
    UploadResponse,
)

app = FastAPI()


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/upload/frontend")


@app.get("/upload/frontend", response_class=FileResponse)
async def upload_frontend():
    html_path_obj: Path = Path(__file__).parent / "html" / "upload.html"
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
        state = load_graph.invoke(state)
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
    file_paths: list[str] = []
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
        file_paths.append(local_file_path)

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
        message="文件上传成功:\n" + "\n".join(file_paths),
        task_ids=task_ids,
    )


@app.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status_view(task_id: str):
    return TaskStatusResponse(
        task_id=task_id,
        status=get_task_status(task_id),
        done_list=get_done_task_list(task_id),
        running_list=get_running_task_list(task_id),
    )


@app.get("/query/health")
async def health():
    logger.info(f"{datetime.now()}: health check success")
    return {"ok": True}


@app.get("/query/frontend", response_class=FileResponse)
async def query_frontend():
    html_path_obj: Path = Path(__file__).parent / "html" / "query.html"
    return FileResponse(
        path=html_path_obj,
        media_type=guess_type(html_path_obj.name)[0],
    )


@app.get("/query/stream/{session_id}")
def query_stream(session_id: str, request: Request):
    logger.info(f"{datetime.now()}: query stream success - {session_id}")
    return StreamingResponse(
        sse_generator(session_id, request),
        media_type="text/event-stream",
    )


def query_graph_task(session_id: str, query: str, is_stream: bool = False):
    state = create_query_default_state(
        session_id=session_id,
        original_query=query,
        is_stream=is_stream,
    )
    logger.info("------------------提问开始查询------------------\n")

    try:
        update_task_status(session_id, "processing", is_stream)
        state = query_graph.invoke(state)
        update_task_status(session_id, "completed", is_stream)

        # 流式: SSE推送成功结果
        if is_stream:
            push_to_session(
                session_id=session_id,
                event=SSEEvent.FINAL,
                data={
                    "answer": state.get("answer", ""),
                    "image_urls": state.get("image_urls", []),
                    "error": None,
                },
            )
        logger.info("------------------提问查询结束------------------\n")
        # 非流式: 直接返回结果state
        return state
    except Exception as e:
        update_task_status(session_id, "failed", is_stream)
        logger.exception(f"提问执行查询图流程失败({session_id}): {e!s}")

        # 无论是否流式, 都直接推送失败结果
        push_to_session(
            session_id=session_id,
            event=SSEEvent.ERROR,
            data={
                "answer": "",
                "image_urls": [],
                "error": str(e),
            },
        )
        # 非流式: 返回带 error 的 state, 避免上层解包 None
        return {"answer": "", "image_urls": [], "error": str(e)}


@app.post("/query")
async def query(background_tasks: BackgroundTasks, params: QueryRequest):
    session_id, query, is_stream = params.session_id, params.query, params.is_stream
    logger.info(
        f"session_id: {session_id}, 用户发出提问: {query}, 是否流式返回: {is_stream}"
    )

    # 清理task: 清空历史对话中所有task的上游数据
    clear_task(session_id)
    if is_stream:
        # 清理SSE: 每次对话都要创建一个SSE的新队列, 覆盖SSE的旧队列
        # 执行异步任务之前就要准备好新队列, 避免 EventSource 连接竞态
        create_sse_queue(session_id)

        # 流式异步执行
        background_tasks.add_task(
            func=query_graph_task,
            session_id=session_id,
            query=query,
            is_stream=is_stream,
        )
        return QueryAsyncResponse(
            session_id=session_id,
            message=f"{session_id} 问题正在解析中...",
        )
    else:
        # 非流式同步执行
        state = query_graph_task(session_id, query, is_stream)
        return QuerySyncResponse(
            session_id=session_id,
            message=f"{session_id} 问题解析完成",
            answer=state.get("answer", ""),
            image_urls=state.get("image_urls", []),
            done_list=get_done_task_list(session_id),
            error=state.get("error", ""),
        )


if __name__ == "__main__":
    uvicorn.run(
        app="project.rag_knowledge.app.api.server:app",
        host="127.0.0.1",
        port=8100,
        reload=True,
    )
