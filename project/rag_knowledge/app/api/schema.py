from pydantic import BaseModel


class CommonResponse(BaseModel):
    code: int = 0
    message: str = "success"


class UploadResponse(CommonResponse):
    task_ids: list[str] = []


class TaskStatusResponse(CommonResponse):
    task_id: str = ""
    status: str = ""
    done_list: list[str] = []
    running_list: list[str] = []


class QueryRequest(BaseModel):
    session_id: str = ""
    query: str = ""
    is_stream: bool = False


class QueryAsyncResponse(CommonResponse):
    session_id: str = ""


class QuerySyncResponse(CommonResponse):
    session_id: str = ""
    answer: str = ""
    image_urls: list[str] = []
    done_list: list[str] = []
    error: str = ""
