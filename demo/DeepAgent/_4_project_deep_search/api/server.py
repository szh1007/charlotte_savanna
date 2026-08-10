import asyncio
import os
import shutil
import sys
import uuid

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add project root to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import agent runner and monitor
# 注意: agent.main_agent 导入时会初始化 main_agent, 这可能需要几秒钟

from agent.main_agent import run_deep_agent  # noqa: E402

from api.monitor import monitor  # noqa: E402

app = FastAPI(title="DeepAgents API")

# 挂载输出目录, 以便前端访问生成的静态文件
# 假设输出目录位于项目根目录下的 output
output_dir = os.path.join(project_root, "output")
if not os.path.exists(output_dir):
    os.makedirs(output_dir)
app.mount("/outputs", StaticFiles(directory=output_dir), name="outputs")

# 定义上传目录 updated
updated_dir = os.path.join(project_root, "updated")
if not os.path.exists(updated_dir):
    os.makedirs(updated_dir)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        # 延迟绑定 loop, 防止初始化时 loop 不一致
        self.loop = None

    def set_loop(self, loop):
        self.loop = loop
        monitor.set_websocket_manager(self)

    async def connect(self, websocket: WebSocket, thread_id: str):
        await websocket.accept()
        self.active_connections[thread_id] = websocket
        print(f"Client connected: {thread_id}")

    def disconnect(self, websocket: WebSocket, thread_id: str):
        if thread_id in self.active_connections:
            del self.active_connections[thread_id]
        print(f"Client disconnected: {thread_id}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def send_to_thread(self, message: dict, thread_id: str):
        if thread_id in self.active_connections:
            websocket = self.active_connections[thread_id]
            await websocket.send_json(message)


manager = ConnectionManager()


@app.on_event("startup")
async def startup_event():
    # 在应用启动时, 将正确的事件循环绑定到 manager
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    print(f"[Server] WebSocket Manager bound to loop: {id(loop)}")


class TaskRequest(BaseModel):
    query: str
    thread_id: str = None


@app.post("/api/task")
async def run_task(request: TaskRequest):
    """
    启动一个新的 Agent 任务
    """
    thread_id = request.thread_id or str(uuid.uuid4())

    # 异步运行 Agent, 不阻塞主线程
    # 注意: 这里简单的使用 asyncio.create_task 可能无法捕获所有错误
    # 在生产环境中建议使用 Celery 或其他任务队列
    background_task = asyncio.create_task(  # noqa: F841, RUF006
        run_deep_agent(request.query, thread_id)
    )

    return {"status": "started", "thread_id": thread_id}


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...), thread_id: str = Form(...)):
    """
    上传文件到 updated/session_{thread_id} 目录
    """
    target_dir = os.path.join(updated_dir, f"session_{thread_id}")
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    saved_files = []
    for file in files:
        file_path = os.path.join(target_dir, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(file.filename)

    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(path: str):
    """
    下载指定文件
    path: 绝对路径
    """
    # 安全检查
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(output_dir)):
        return {"error": "Access denied: Path must be within output directory"}

    if not os.path.exists(abs_path):
        return {"error": "File not found"}

    return FileResponse(abs_path, filename=os.path.basename(abs_path))


@app.get("/api/files")
async def list_files(path: str):
    """
    列出指定目录下的文件
    path: 绝对路径, 必须在 output 目录下
    """
    # 安全检查: 确保路径在 output_dir 下
    print(f"[DEBUG] list_files request path: {path}")
    abs_path = os.path.abspath(path)
    output_abs = os.path.abspath(output_dir)
    print(f"[DEBUG] abs_path: {abs_path}")
    print(f"[DEBUG] output_dir abs: {output_abs}")

    # 使用 os.path.commonpath 或转为小写比较来处理 Windows 路径大小写问题
    try:
        # 在 Windows 上, 路径大小写可能不一致, 使用 normcase 标准化
        if sys.platform == "win32":
            check_path = os.path.normcase(abs_path)
            check_output = os.path.normcase(output_abs)
        else:
            check_path = abs_path
            check_output = output_abs

        if not check_path.startswith(check_output):
            print(f"[ERROR] Access denied. {check_path} not startswith {check_output}")
            return {"error": "Access denied: Path must be within output directory"}
    except Exception as e:
        print(f"[ERROR] Path check failed: {e}")
        return {"error": f"Path check failed: {e}"}

    if not os.path.exists(abs_path):
        print(f"[ERROR] Path not found: {abs_path}")
        return {"error": "Path not found"}

    files = []
    try:
        # 使用 os.walk 递归遍历目录
        for root, dirs, filenames in os.walk(abs_path):
            for filename in filenames:
                file_path = os.path.join(root, filename)

                # 计算相对于 output_dir 的路径, 用于生成 URL (保留, 虽然下载用绝对路径)
                rel_path = os.path.relpath(file_path, output_dir)
                url_path = rel_path.replace("\\", "/")

                files.append(
                    {
                        "name": filename,
                        "type": "file",
                        "path": file_path,
                        "url": f"/outputs/{url_path}",
                        "size": os.path.getsize(file_path),
                        "mtime": os.path.getmtime(file_path),
                    }
                )

    except Exception as e:
        print(f"[ERROR] Walk failed: {e}")
        return {"error": str(e)}

    # 按时间倒序排列
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] Found {len(files)} files")
    return {"files": files}


@app.websocket("/ws")
async def websocket_legacy(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json(
        {"type": "error", "message": "Client outdated. Please refresh page."}
    )
    await websocket.close(code=1000, reason="Client outdated")


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    await manager.connect(websocket, thread_id)
    try:
        while True:
            # 保持连接活跃, 并可以接收前端指令
            # 目前只作为简单的保活 echo
            data = await websocket.receive_text()
            await websocket.send_json({"type": "pong", "message": f"received: {data}"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, thread_id)
    except Exception as e:
        print(f"WebSocket Error: {e}")
        manager.disconnect(websocket, thread_id)


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
