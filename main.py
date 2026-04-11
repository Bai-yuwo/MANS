"""
MANS 系统主入口

FastAPI 应用核心，负责：
1. SSE 流式事件推送接口
2. 命令触发接口（异步后台执行）
"""

import json
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from core.event_bus import event_bus
from agents.writer.logic import WriterAgent


# ============================================================
# FastAPI 应用生命周期管理
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用上下文管理器
    
    管理 FastAPI 应用的启动和关闭事件。
    这里主要用于确保事件总线正确初始化。
    """
    # 应用启动时执行（当前无需额外操作）
    yield
    # 应用关闭时执行
    await event_bus.close()


# ============================================================
# FastAPI 应用实例
# ============================================================

app = FastAPI(
    title="MANS API",
    description="多智能体小说系统后端接口",
    version="0.1.0",
    lifespan=lifespan
)

# ----------------------------------------------------
# 跨域中间件配置
# 允许所有来源跨域，方便本地调试
# ----------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # 允许所有来源
    allow_credentials=True,      # 允许携带凭证
    allow_methods=["*"],        # 允许所有 HTTP 方法
    allow_headers=["*"],        # 允许所有请求头
)


# ============================================================
# SSE 流式事件接口
# ============================================================

@app.get("/api/v1/stream")
async def stream_events():
    """
    SSE 流式事件推送接口
    
    客户端通过此接口建立长连接，实时接收系统事件。
    事件格式遵循 Server-Sent Events (SSE) 规范。
    
    返回:
        StreamingResponse: SSE 流，包含所有发布的事件
    """
    async def event_generator():
        """
        异步事件生成器
        
        订阅事件总线，将每个事件转换为 SSE 格式并 yield。
        """
        # 订阅事件流
        async for event in event_bus.subscribe():
            # 转换为 SSE 格式: "data: {json}\n\n"
            sse_data = f"data: {json.dumps({
                "event_type": event.event_type.value,
                "payload": event.payload,
                "event_id": event.event_id,
                "timestamp": event.timestamp.isoformat()
            }, ensure_ascii=False)}\n\n"
            yield sse_data
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            # 保持连接不关闭
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用 Nginx 缓冲
        }
    )


# ============================================================
# 命令触发接口
# ============================================================

@app.post("/api/v1/command")
async def dispatch_command(command_data: dict, background_tasks: BackgroundTasks):
    """
    命令触发接口
    
    接收前端命令请求，将任务分发到后台异步执行。
    接口立即返回，不会阻塞 HTTP 请求。
    
    请求体:
        {
            "command": "DRAFT_CHAPTER",
            "payload": {"plot": "情节描述"}
        }
    
    返回:
        dict: 状态信息
    """
    command = command_data.get("command")
    payload = command_data.get("payload", {})
    
    # 路由分发：根据命令类型执行对应逻辑
    if command == "DRAFT_CHAPTER":
        # 实例化写作智能体
        agent = WriterAgent()
        # 将任务添加到后台异步执行，不阻塞当前请求
        background_tasks.add_task(agent.run, payload, event_bus)
    else:
        # 未知命令，返回错误信息
        return {
            "status": "error",
            "message": f"Unknown command: {command}"
        }
    
    # 立即返回，表示命令已分发
    return {
        "status": "processing",
        "message": "Command dispatched"
    }


# ============================================================
# 健康检查接口
# ============================================================

@app.get("/health")
async def health_check():
    """
    健康检查接口
    
    用于负载均衡器和容器编排探测服务状态。
    """
    return {"status": "healthy", "service": "MANS API"}


# ============================================================
# 直接运行入口（开发调试用）
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "mans_system.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
