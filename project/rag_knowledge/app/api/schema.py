from pydantic import BaseModel


class CommonResponse(BaseModel):
    code: int = 0
    message: str = "success"


class UploadResponse(CommonResponse):
    task_ids: list[str] = []


class UploadStatusResponse(CommonResponse):
    task_id: str = ""
    status: str = ""
    done_list: list[str] = []
    running_list: list[str] = []
