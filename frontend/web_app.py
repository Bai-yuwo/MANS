"""
frontend/web_app.py — MANS Web 服务入口(v2 精简版)

职责:
    1. 挂载 /api/v2 路由(17-Agent 新架构)
    2. 静态文件服务(frontend/ 目录)
    3. 根入口 / 与 /v2 返回 v2 前端

旧 API(/api/projects/...)与旧前端(index.html + app.js)已在 P5 移除。
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.config import get_config
from core.logging_config import get_logger, setup_sse_logging

# 新架构 v2 API
from api.v2 import router as v2_router
from api.session_manager import get_session_manager

# 日志初始化
logger = get_logger("frontend.web_app")
setup_sse_logging()

# 全局配置
_config = get_config()

# ============================================================
# FastAPI 应用
# ============================================================
app = FastAPI(title="MANS - Multi-Agent Novel System (v2)")

# 挂载新架构路由
app.include_router(v2_router)


@app.on_event("startup")
async def _startup():
    """启动后台清理协程,自动清理超时会话。"""
    await get_session_manager().start_cleanup_loop()
    logger.info("SessionManager cleanup loop 已启动")

# ============================================================
# 根入口
# ============================================================
@app.get("/")
async def root():
    """返回 v2 前端入口。"""
    return FileResponse(Path(__file__).parent / "v2.html")


@app.get("/v2")
async def root_v2():
    """v2 前端入口(与 / 相同)。"""
    return FileResponse(Path(__file__).parent / "v2.html")


# ============================================================
# 静态文件
# ============================================================
frontend_dir = Path(__file__).parent
app.mount("/frontend", StaticFiles(directory=frontend_dir), name="frontend")
