"""
MANS 系统主入口

FastAPI 应用核心，负责：
1. 托管前端静态页面
2. SSE 流式事件推送接口
3. 命令触发接口
"""

import json
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from core.event_bus import event_bus
from agents.writer.logic import WriterAgent

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await event_bus.close()

app = FastAPI(
    title="MANS API",
    description="多智能体小说系统后端接口",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# API 接口路由
# ============================================================

@app.get("/api/v1/stream")
async def stream_events():
    """SSE 流式事件推送接口"""
    async def event_generator():
        async for event in event_bus.subscribe():
            sse_data = f"data: {json.dumps({
                'event_type': event.event_type.value,
                'payload': event.payload,
                'event_id': event.event_id,
                'timestamp': event.timestamp.isoformat()
            }, ensure_ascii=False)}\n\n"
            yield sse_data

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.post("/api/v1/command")
async def dispatch_command(command_data: dict, background_tasks: BackgroundTasks):
    """命令触发接口"""
    command = command_data.get("command")
    payload = command_data.get("payload", {})

    if command == "DRAFT_CHAPTER":
        agent = WriterAgent()
        background_tasks.add_task(agent.run, payload, event_bus)
    else:
        return {"status": "error", "message": f"未知命令: {command}"}

    return {"status": "processing", "message": "命令已分发"}

# ============================================================
# 前端静态资源托管 (必须放在 API 路由之后)
# ============================================================

# 获取前端目录绝对路径
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")

@app.get("/")
async def serve_index():
    """访问根目录时返回 index.html"""
    return FileResponse(os.path.join(frontend_dir, "index.html"))

# 挂载 frontend 文件夹下的其他静态资源 (如 app.js)
app.mount("/", StaticFiles(directory=frontend_dir), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)