"""
frontend/web_app.py —— MANS Web 服务入口

本文件是整个系统对外的唯一 HTTP 门面，承担以下职责：
    1. RESTful API：项目生命周期（创建 / 删除 / 查询）、初始化流程（Bible / 人物 / 大纲）、
       写作流程（场景生成 / 编辑 / 确认）、知识库查询、Issue Pool 聚合等。
    2. SSE 流式输出：所有涉及 LLM 的长耗时操作（生成 Bible、人物、大纲、弧线规划、
       场景写作、重写）均提供 Server-Sent Events 接口，前端可实时观察打字机效果。
    3. 静态文件托管：挂载 frontend/ 目录下的 HTML、CSS、JS，提供单页应用入口。
    4. 跨模块编排：协调 StoryDB、CharacterDB、BibleDB、ForeshadowingDB、VectorStore、
       UpdateExtractor、InjectionEngine、Writer 等核心组件完成复杂业务流。

安全设计：
    - 路径遍历防护：_validate_project_path() 通过 resolve() + relative_to() 确保所有
      文件操作被限制在 workspace/ 目录内。
    - 所有文件操作使用 aiofiles 异步 IO，避免阻塞事件循环。

路由命名规范：
    - /api/projects/{project_id}/...          —— 项目级操作
    - /api/projects/{project_id}/generate/... —— 触发 LLM 生成（非流式兼容）
    - /api/projects/{project_id}/stream/...   —— SSE 流式生成
    - /api/projects/{project_id}/chapters/... —— 章节与场景操作
"""

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime

import aiofiles
import aiofiles.os
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# 核心数据模型与配置
from core.schemas import (
    ProjectMeta, ScenePlan, ChapterPlan,
    CharacterCard, WorldRule, ForeshadowingItem
)
from core.config import get_config
from core.logging_config import get_logger, log_exception, sse_log_handler, setup_sse_logging

# 向量存储与状态提取
from vector_store.store import VectorStore
from core.update_extractor import UpdateExtractor

# 日志初始化：注册 SSE 日志处理器，使后端日志可推送到前端监控面板
logger = get_logger('frontend.web_app')
setup_sse_logging()

# 全局配置实例，供路由中读取角色默认值
_config = get_config()

# 生成器组件：按依赖顺序 Bible → Characters → Outline → Arc → Chapter
from generators import (
    BibleGenerator, CharacterGenerator, OutlineGenerator,
    ArcPlanner, ChapterPlanner
)

# 写作核心：唯一调用主 LLM 生成正文的组件
from writer import Writer

# 知识库访问层
from knowledge_bases.bible_db import BibleDB
from knowledge_bases.character_db import CharacterDB
from knowledge_bases.story_db import StoryDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB
from knowledge_bases.style_db import StyleDB

# 注入引擎与快速 LLM 调用
from core.llm_client import quick_call
from core.injection_engine import InjectionEngine

# ============================================================
# FastAPI 应用实例
# ============================================================
app = FastAPI(title="MANS - Multi-Agent Novel System")

# ============================================================
# 安全辅助
# ============================================================

# 工作区根目录的绝对路径，用于路径遍历校验
_workspace_config_path = Path(_config.WORKSPACE_PATH)
if not _workspace_config_path.is_absolute():
    _WORKSPACE_ROOT = _workspace_config_path.resolve()
else:
    _WORKSPACE_ROOT = _workspace_config_path


def _validate_project_path(project_id: str) -> Path:
    """
    校验项目路径安全，防止路径遍历攻击。

    实现原理：
        1. 将用户传入的 project_id 与 _WORKSPACE_ROOT 拼接后调用 resolve() 解析为绝对路径。
        2. 尝试用 relative_to(_WORKSPACE_ROOT) 判断该路径是否在工作区内。
        3. 若路径跳出工作区（如 project_id 包含 "../"），relative_to() 抛出 ValueError，
           此时返回 HTTP 403 拒绝访问。

    Args:
        project_id: 用户请求中的项目标识字符串。

    Returns:
        经安全校验后的项目目录 Path 对象。

    Raises:
        HTTPException: 403，当路径遍历被检测到时。
    """
    project_path = (_WORKSPACE_ROOT / project_id).resolve()
    try:
        project_path.relative_to(_WORKSPACE_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="非法项目ID")
    return project_path


# ============================================================
# Pydantic 请求/响应模型
# ============================================================

class CreateProjectRequest(BaseModel):
    """创建项目接口的请求体模型。

    字段说明：
        name: 作品名称，必填，用于展示与文件组织。
        genre: 题材类型，默认"玄幻"，决定 BibleGenerator 的生成方向。
        core_idea: 核心创意，一句话概括故事灵魂，是生成 Bible 的首要依据。
        protagonist_seed: 主角起点设定，如"山村少年，天生废灵根"。
        target_length: 目标篇幅，影响大纲的章节数量和节奏设计。
        tone: 整体基调，如"热血励志""轻松搞笑"。
        style_reference: 文风参考作家或作品，供 Writer 模仿。
        forbidden_elements: 禁止出现的元素列表，如"穿越、系统"。
    """
    name: str
    genre: str = "玄幻"
    core_idea: str
    protagonist_seed: str
    target_length: str = "中篇(10-50万)"
    tone: str = ""
    style_reference: str = ""
    forbidden_elements: list[str] = []


class CreateArcRequest(BaseModel):
    """创建弧线接口的请求体模型。

    字段说明：
        arc_number: 弧线编号，可选；不传时由系统自动分配最小可用序号。
        title: 弧线名称，如"宗门试炼"。
        chapter_range: 章节范围，二元整数列表 [起始章, 结束章]。
        description: 弧线核心走向或作用的一句话描述。
    """
    arc_number: Optional[int] = None
    title: str
    chapter_range: list[int]
    description: str


class GenerateResponse(BaseModel):
    """通用生成操作的响应包装模型。"""
    success: bool
    message: str
    data: Optional[dict] = None


class ProjectStatusResponse(BaseModel):
    """项目状态查询的响应模型，覆盖初始化三要素与当前写作进度。"""
    project_id: str
    status: str
    current_chapter: int
    initialized: bool
    has_bible: bool
    has_characters: bool
    has_outline: bool


# ============================================================
# 项目管理接口
# ============================================================

@app.post("/api/projects")
async def create_project(request: CreateProjectRequest):
    """
    创建新项目。

    执行流程：
        1. 生成 UUID 作为项目唯一标识。
        2. 在 workspace/{project_id}/ 下创建标准目录结构：
           characters/（人物卡片）、chapters/（章节草稿）、arcs/（弧线规划）。
           vector_store/ 由 VectorStore 类惰性创建，无需预建。
        3. 实例化 ProjectMeta Pydantic 模型，持久化为 project_meta.json。
        4. 项目初始状态为 "initializing"，current_chapter=0。

    Args:
        request: CreateProjectRequest 请求体。

    Returns:
        JSON 对象，包含 project_id 与创建成功消息。

    Raises:
        HTTPException: 500，当文件写入异常时。
    """
    project_id = str(uuid.uuid4())
    workspace_path = _WORKSPACE_ROOT / project_id

    try:
        # 创建项目目录骨架
        (workspace_path / "characters").mkdir(parents=True, exist_ok=True)
        (workspace_path / "chapters").mkdir(parents=True, exist_ok=True)
        (workspace_path / "arcs").mkdir(parents=True, exist_ok=True)

        # 构造并持久化项目元数据
        project_meta = ProjectMeta(
            id=project_id,
            name=request.name,
            genre=request.genre,
            core_idea=request.core_idea,
            protagonist_seed=request.protagonist_seed,
            target_length=request.target_length,
            tone=request.tone,
            style_reference=request.style_reference,
            forbidden_elements=request.forbidden_elements,
            status="initializing",
            current_chapter=0
        )

        meta_path = workspace_path / "project_meta.json"
        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(project_meta.model_dump(), ensure_ascii=False, indent=2))

        return {
            "success": True,
            "project_id": project_id,
            "message": "项目创建成功"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建项目失败: {str(e)}")


@app.get("/api/projects")
async def get_projects():
    """
    获取所有项目列表。

    扫描 workspace/ 下的所有子目录，读取每个目录中的 project_meta.json，
    提取 id、name、genre、status、current_chapter 等关键字段返回。
    若某个项目的 meta 文件损坏或不存在，则静默跳过，避免影响其他项目展示。

    Returns:
        {"projects": [...]}，项目列表按目录遍历顺序（无序）。
    """
    workspace_path = _WORKSPACE_ROOT
    projects = []

    if workspace_path.exists():
        for project_dir in workspace_path.iterdir():
            if project_dir.is_dir():
                meta_path = project_dir / "project_meta.json"
                if meta_path.exists():
                    try:
                        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
                            content = await f.read()
                            meta = json.loads(content)
                        projects.append({
                            "id": meta.get("id", project_dir.name),
                            "name": meta.get("name", "未命名"),
                            "genre": meta.get("genre", ""),
                            "status": meta.get("status", "unknown"),
                            "current_chapter": meta.get("current_chapter", 0),
                            "created_at": meta.get("created_at", "")
                        })
                    except Exception:
                        pass

    return {"projects": projects}


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    """
    获取单个项目的完整元数据。

    Args:
        project_id: 项目 UUID。

    Returns:
        project_meta.json 的完整内容（字典形式）。

    Raises:
        HTTPException: 404，项目不存在；500，读取失败。
    """
    workspace_path = _validate_project_path(project_id)
    meta_path = workspace_path / "project_meta.json"

    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)
        return meta
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取项目失败: {str(e)}")


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """
    删除项目及其所有数据（不可恢复）。

    先通过 _validate_project_path() 校验路径安全，再使用 shutil.rmtree() 递归删除。

    Args:
        project_id: 项目 UUID。

    Returns:
        {"success": True, "message": "项目已删除"}。

    Raises:
        HTTPException: 404，项目不存在；500，删除失败。
    """
    import shutil

    project_path = _validate_project_path(project_id)

    if not project_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        shutil.rmtree(project_path)
        return {"success": True, "message": "项目已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除项目失败: {str(e)}")


@app.get("/api/projects/{project_id}/status")
async def get_project_status(project_id: str):
    """
    获取项目初始化与写作综合状态。

    判断逻辑：
        initialized = has_bible AND has_characters AND has_outline
        即只有当 Bible、人物卡、大纲三者全部存在时，项目才被视为初始化完成。

    Args:
        project_id: 项目 UUID。

    Returns:
        字典，包含 has_bible、has_characters、has_outline、initialized、
        current_chapter、status 等字段。
    """
    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    meta_path = workspace_path / "project_meta.json"

    try:
        status = {
            "project_id": project_id,
            "initialized": False,
            "has_bible": (workspace_path / "bible" / "bible.json").exists(),
            "has_characters": (workspace_path / "characters").exists() and any(
                (workspace_path / "characters").glob("*.json")
            ),
            "has_outline": (workspace_path / "story" / "outline.json").exists(),
            "current_chapter": 0,
            "status": "unknown"
        }

        if meta_path.exists():
            async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
                content = await f.read()
                meta = json.loads(content)
            status["current_chapter"] = meta.get("current_chapter", 0)
            status["status"] = meta.get("status", "unknown")

        status["initialized"] = (
            status["has_bible"] and
            status["has_characters"] and
            status["has_outline"]
        )

        return status

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}")


# ============================================================
# 初始化流程接口
# ============================================================

@app.post("/api/projects/{project_id}/generate/bible")
async def generate_bible(project_id: str, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    触发 Bible（世界观设定）生成 —— 非流式兼容接口。

    执行流程：
        1. 读取 project_meta.json 构造 ProjectMeta 对象。
        2. 实例化 BibleGenerator，设置进度回调函数收集中间状态。
        3. 调用 generator.generate() 执行生成，结果自动持久化到 bible.json。
        4. 返回生成结果与进度消息列表。

    Args:
        project_id: 项目 UUID。
        temperature: LLM 采样温度，默认 0.7。

    Returns:
        {"success": True, "message": "...", "data": {...}, "progress": [...]}。

    Raises:
        HTTPException: 404，项目不存在；500，生成失败。
    """
    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)

        project_meta = ProjectMeta(**meta)

        generator = BibleGenerator(project_id)

        # 进度回调：收集生成过程中的关键状态消息，用于前端展示
        progress_messages = []
        def progress_callback(msg: str):
            progress_messages.append(msg)
            logger.info(f"[BibleGenerator] {msg}")

        generator.set_progress_callback(progress_callback)
        result = await generator.generate(project_meta=project_meta, temperature=temperature)

        return {
            "success": True,
            "message": "Bible 生成成功",
            "data": result,
            "progress": progress_messages
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成 Bible 失败: {str(e)}")


@app.post("/api/projects/{project_id}/stream/bible")
async def stream_generate_bible(project_id: str, request: Request, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    流式生成 Bible（SSE 接口）。

    相比非流式接口，本端点通过 EventSourceResponse 将 LLM 的每个 token 实时推送给前端，
    实现打字机效果。事件类型包括：
        start    —— 生成开始
        progress —— 阶段进度消息
        token    —— LLM 原始输出片段
        complete —— 生成完成，附带完整结果
        error    —— 异常信息
        done     —— SSE 流结束标志

    Args:
        project_id: 项目 UUID。
        request: FastAPI Request 对象，用于检测客户端断开。
        temperature: LLM 采样温度。

    Returns:
        EventSourceResponse，SSE 事件流。
    """
    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    async def event_generator():
        """内部 SSE 事件生成器协程。"""
        try:
            async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
                content = await f.read()
                meta = json.loads(content)

            project_meta = ProjectMeta(**meta)
            generator = BibleGenerator(project_id)

            # 通过生成器的流式接口逐事件转发
            async for event in generator.generate_stream(project_meta=project_meta, temperature=temperature):
                event_type = event.get("type", "message")

                if event_type == "start":
                    yield {
                        "event": "start",
                        "data": json.dumps({
                            "message": event.get("message", ""),
                            "prompt_length": event.get("prompt_length", 0),
                            "model": event.get("model", ""),
                            "role": event.get("role", ""),
                            "max_tokens": event.get("max_tokens", 0),
                            "temperature": event.get("temperature", 0),
                        }, ensure_ascii=False)
                    }
                elif event_type == "progress":
                    yield {
                        "event": "progress",
                        "data": json.dumps({"message": event.get("message", "")}, ensure_ascii=False)
                    }
                elif event_type == "token":
                    yield {
                        "event": "token",
                        "data": json.dumps({"content": event.get("content", "")}, ensure_ascii=False)
                    }
                elif event_type == "complete":
                    yield {
                        "event": "complete",
                        "data": json.dumps({
                            "message": event.get("message", "生成完成"),
                            "data": event.get("data", {})
                        }, ensure_ascii=False)
                    }
                elif event_type == "error":
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": event.get("error", "未知错误")}, ensure_ascii=False)
                    }

            # 推送流结束标志，前端据此关闭 EventSource
            yield {
                "event": "done",
                "data": json.dumps({"message": "流式传输完成"})
            }

        except Exception as e:
            logger.error(f"流式生成 Bible 失败: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)}, ensure_ascii=False)
            }
            return

    return EventSourceResponse(event_generator(), ping=15)


@app.post("/api/projects/{project_id}/confirm/bible")
async def confirm_bible(project_id: str):
    """
    用户确认 Bible。

    当前为占位接口，可扩展为版本标记（如将 bible.json 标记为 v1 锁定）。
    确认后 Bible 进入只读状态，后续生成以该版本为准。

    Returns:
        {"success": True, "message": "Bible 已确认"}。
    """
    return {"success": True, "message": "Bible 已确认"}


@app.put("/api/projects/{project_id}/bible")
async def update_bible(project_id: str, bible_data: dict):
    """
    用户手动修改 Bible 内容。

    用于前端编辑器中用户直接编辑世界观设定后，将修改同步回 bible.json。
    不触发 LLM，仅做持久化。

    Args:
        project_id: 项目 UUID。
        bible_data: 完整的 Bible 字典数据。

    Returns:
        {"success": True, "message": "Bible 已更新"}。

    Raises:
        HTTPException: 500，保存失败。
    """
    _validate_project_path(project_id)
    try:
        bible_db = BibleDB(project_id)
        await bible_db.save("bible", bible_data)
        return {"success": True, "message": "Bible 已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新 Bible 失败: {str(e)}")


@app.post("/api/projects/{project_id}/generate/characters")
async def generate_characters(project_id: str, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    触发人物设定生成。

    前置条件：项目必须已存在 Bible，否则返回 400。
    执行流程：
        1. 读取 ProjectMeta 与 Bible 数据。
        2. 实例化 CharacterGenerator。
        3. 生成主角、配角及关系网络，自动持久化到 characters/ 目录。

    Args:
        project_id: 项目 UUID。
        temperature: LLM 采样温度。

    Returns:
        {"success": True, "message": "人物生成成功", "data": {...}}。

    Raises:
        HTTPException: 400，未生成 Bible；500，生成失败。
    """
    try:
        workspace_path = _validate_project_path(project_id)
        async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)
        project_meta = ProjectMeta(**meta)

        bible_db = BibleDB(project_id)
        bible_data = await bible_db.load("bible")
        if not bible_data:
            raise HTTPException(status_code=400, detail="请先生成 Bible")

        generator = CharacterGenerator(project_id)
        result = await generator.generate(
            project_meta=project_meta,
            bible_data=bible_data,
            temperature=temperature
        )

        return {
            "success": True,
            "message": "人物生成成功",
            "data": result
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成人物失败: {str(e)}")


@app.post("/api/projects/{project_id}/confirm/characters")
async def confirm_characters(project_id: str):
    """用户确认人物设定（占位接口，可扩展为锁定逻辑）。"""
    return {"success": True, "message": "人物设定已确认"}


@app.post("/api/projects/{project_id}/generate/outline")
async def generate_outline(project_id: str, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    触发全局大纲生成。

    前置条件：项目必须已存在 Bible 和人物设定。
    执行流程：
        1. 读取 ProjectMeta、Bible、人物数据。
        2. 从 characters/ 目录扫描 .json 文件，区分 protagonist 与 supporting_characters。
        3. 实例化 OutlineGenerator 生成三幕结构、转折点、全局伏笔。
        4. 结果持久化到 story/outline.json。

    Args:
        project_id: 项目 UUID。
        temperature: LLM 采样温度。

    Returns:
        {"success": True, "message": "大纲生成成功", "data": {...}}。

    Raises:
        HTTPException: 400，前置数据缺失；500，生成失败。
    """
    try:
        workspace_path = _validate_project_path(project_id)
        async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)
        project_meta = ProjectMeta(**meta)

        bible_db = BibleDB(project_id)
        bible_data = await bible_db.load("bible")
        if not bible_data:
            raise HTTPException(status_code=400, detail="请先生成 Bible")

        # 从文件系统扫描人物卡片
        character_db = CharacterDB(project_id)
        characters_data = {
            "protagonist": {},
            "supporting_characters": []
        }

        char_files = list((workspace_path / "characters").glob("*.json"))
        for char_file in char_files:
            if char_file.name != "relationships.json":
                async with aiofiles.open(char_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                    char_data = json.loads(content)
                if char_data.get("is_protagonist", False):
                    characters_data["protagonist"] = char_data
                else:
                    characters_data["supporting_characters"].append(char_data)

        # 兼容旧数据：若未找到标记为主角的卡片，将第一个配角提升为主角
        if not characters_data["protagonist"] and characters_data["supporting_characters"]:
            characters_data["protagonist"] = characters_data["supporting_characters"].pop(0)

        generator = OutlineGenerator(project_id)
        result = await generator.generate(
            project_meta=project_meta,
            bible_data=bible_data,
            characters_data=characters_data,
            temperature=temperature
        )

        return {
            "success": True,
            "message": "大纲生成成功",
            "data": result
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成大纲失败: {str(e)}")


@app.post("/api/projects/{project_id}/confirm/outline")
async def confirm_outline(project_id: str):
    """
    用户确认大纲，正式进入写作阶段。

    确认后更新 project_meta.json 中的 status 为 "writing"，
    前端据此开放写作面板与场景生成按钮。

    Returns:
        {"success": True, "message": "大纲已确认，进入写作阶段"}。

    Raises:
        HTTPException: 500，文件写入失败。
    """
    try:
        workspace_path = _validate_project_path(project_id)
        meta_path = workspace_path / "project_meta.json"

        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)

        meta["status"] = "writing"

        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))

        return {"success": True, "message": "大纲已确认，进入写作阶段"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"确认大纲失败: {str(e)}")


# ============================================================
# 弧线管理接口
# ============================================================

@app.get("/api/projects/{project_id}/arcs")
async def list_arcs(project_id: str):
    """
    获取项目下所有弧线规划的列表。

    从 StoryDB 读取 arcs/ 目录下的所有弧线文件，返回包含 arc_number、
    title、chapter_range、description 等字段的列表。

    Args:
        project_id: 项目 UUID。

    Returns:
        {"project_id": "...", "arcs": [...], "count": N}。

    Raises:
        HTTPException: 404，项目不存在。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    story_db = StoryDB(project_id)
    arcs = await story_db.list_arc_plans()
    return {
        "project_id": project_id,
        "arcs": arcs,
        "count": len(arcs)
    }


@app.get("/api/projects/{project_id}/arcs/{arc_number}/status")
async def get_arc_status(project_id: str, arc_number: int):
    """
    检查指定弧线规划是否已生成。

    直接检查 arcs/arc_{arc_number}.json 文件是否存在，
    用于前端在显示弧线列表时区分"已生成"与"占位符"状态。

    Args:
        project_id: 项目 UUID。
        arc_number: 弧线序号。

    Returns:
        {"project_id": "...", "arc_number": N, "exists": bool}。
    """
    workspace_path = _validate_project_path(project_id)
    arc_path = workspace_path / "arcs" / f"arc_{arc_number}.json"
    return {
        "project_id": project_id,
        "arc_number": arc_number,
        "exists": arc_path.exists()
    }


@app.post("/api/projects/{project_id}/arcs")
async def create_arc_meta(project_id: str, request: CreateArcRequest):
    """
    创建弧线元数据（占位符）。

    当用户在前端点击"创建新弧线"但尚未触发 LLM 生成时，
    先创建一个 is_placeholder=True 的轻量记录，供弧线列表展示。
    若未指定 arc_number，则自动分配最小可用正整数。

    Args:
        project_id: 项目 UUID。
        request: CreateArcRequest 请求体。

    Returns:
        {"success": True, "arc_number": N, "message": "..."}。

    Raises:
        HTTPException: 404，项目不存在。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    story_db = StoryDB(project_id)

    # 自动分配弧线编号：取最小未使用的正整数
    arc_number = request.arc_number
    if arc_number is None:
        existing = await story_db.list_arc_plans()
        numbers = {a.get("arc_number", 0) for a in existing}
        arc_number = 1
        while arc_number in numbers:
            arc_number += 1

    arc_data = {
        "arc_id": f"arc_{arc_number}",
        "arc_number": arc_number,
        "arc_theme": request.title or "未命名弧线",
        "arc_goal": request.description,
        "chapter_range": request.chapter_range,
        "key_directions": [],
        "is_placeholder": True
    }

    await story_db.save_arc_plan(str(arc_number), arc_data)
    return {
        "success": True,
        "arc_number": arc_number,
        "message": f"弧线 {arc_number} 创建成功"
    }


@app.delete("/api/projects/{project_id}/arcs/{arc_number}")
async def delete_arc_meta(project_id: str, arc_number: int):
    """
    删除指定弧线规划（包括已生成的完整规划）。

    Args:
        project_id: 项目 UUID。
        arc_number: 弧线序号。

    Returns:
        {"success": True/False, "message": "..."}。

    Raises:
        HTTPException: 404，项目不存在。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    arc_path = workspace_path / "arcs" / f"arc_{arc_number}.json"
    if arc_path.exists():
        arc_path.unlink()
        return {"success": True, "message": f"弧线 {arc_number} 已删除"}
    return {"success": False, "message": f"弧线 {arc_number} 不存在"}


@app.post("/api/projects/{project_id}/arcs/suggest")
async def suggest_arc(project_id: str, request: Request):
    """
    基于已有大纲和弧线列表，智能推荐下一条弧线。

    实现机制：
        1. 读取全局大纲（三幕结构）和已有弧线列表。
        2. 计算下一条弧线的建议章节范围（默认接续已有弧线末尾 +1，长度 50 章）。
        3. 构造 prompt，调用 extract 角色的 LLM（带 json_schema）生成推荐。
        4. 若 LLM 调用失败，回退到基于大纲幕结构的默认推荐。

    请求体（可选）：
        {"chapter_range": [起始, 结束]} —— 用户可手动指定范围约束。

    Args:
        project_id: 项目 UUID。
        request: FastAPI Request 对象，用于读取可选的请求体。

    Returns:
        {"success": True, "suggestion": {"chapter_range": [...], "title": "...", "description": "..."}}。

    Raises:
        HTTPException: 400，未生成大纲；404，项目不存在。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    story_db = StoryDB(project_id)
    outline = await story_db.get_outline()
    if not outline:
        raise HTTPException(status_code=400, detail="请先生成大纲")

    existing_arcs = await story_db.list_arc_plans()
    existing_arcs.sort(key=lambda a: a.get("arc_number", 0))

    # 读取可选的请求体（用户可手动传入 chapter_range 约束）
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    user_range = body.get("chapter_range")

    # 计算推荐章节范围：默认接续已有弧线，每段 50 章
    last_end = 0
    for arc in existing_arcs:
        cr = arc.get("chapter_range", [0, 0])
        if len(cr) >= 2 and cr[1] > last_end:
            last_end = cr[1]

    if last_end == 0:
        next_start = 1
    else:
        next_start = last_end + 1

    if user_range and len(user_range) >= 2:
        next_start = user_range[0]
        next_end = user_range[1]
    else:
        next_end = next_start + 49

    three_act = outline.get("three_act_structure", {})
    outline_text = json.dumps(three_act, ensure_ascii=False)
    arcs_text = json.dumps(existing_arcs, ensure_ascii=False)

    prompt = f"""基于以下小说大纲和已存在的弧线列表，为第 {next_start} ~ {next_end} 章推荐一条弧线的名称和描述（核心走向/作用）。

大纲三幕结构：
{outline_text}

已有弧线：
{arcs_text}

章节范围：第 {next_start} 章 ~ 第 {next_end} 章。

请输出严格的 JSON。"""

    # json_schema 约束 LLM 输出结构，确保前端能安全解析
    arc_suggest_schema = {
        "name": "arc_suggestion",
        "schema": {
            "type": "object",
            "properties": {
                "chapter_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2
                },
                "title": {
                    "type": "string",
                    "description": "弧线名称（简洁，10字以内）"
                },
                "description": {
                    "type": "string",
                    "description": "用一句话描述这条弧线的核心走向或作用"
                }
            },
            "required": ["chapter_range", "title", "description"],
            "additionalProperties": False
        }
    }

    try:
        response = await quick_call(
            role="extract",
            prompt=prompt,
            max_tokens=500,
            temperature=_config.EXTRACT_TEMPERATURE,
            response_format="json_schema",
            json_schema=arc_suggest_schema
        )
        suggestion = json.loads(response)
        return {"success": True, "suggestion": suggestion}
    except Exception as e:
        logger.error(f"弧线推荐失败: {e}")
        # 降级回退：基于大纲幕结构给出默认推荐
        act_keys = ["act1", "act2a", "act2b", "act3"]
        fallback_idx = min(len(existing_arcs), len(act_keys) - 1)
        act_data = three_act.get(act_keys[fallback_idx], {})
        fallback = {
            "chapter_range": [next_start, next_end],
            "title": act_data.get("name", f"弧线 {len(existing_arcs) + 1}"),
            "description": act_data.get("description", "继续推进剧情")
        }
        return {"success": True, "suggestion": fallback}


async def _resolve_arc_act_data(project_id: str, arc_number: int) -> dict:
    """
    解析弧线生成所需的 act_data。

    优先级：
        1. 若用户已创建占位符弧线（is_placeholder=True），提取其 arc_theme、arc_goal、
           chapter_range、key_directions 作为输入。
        2. 否则回退到大纲三幕结构，按 arc_number 顺序映射到 act1/act2a/act2b/act3。

    Args:
        project_id: 项目 UUID。
        arc_number: 弧线序号（从 1 开始）。

    Returns:
        act_data 字典，包含 name、description、chapter_range、key_directions。

    Raises:
        HTTPException: 400，未生成大纲。
    """
    story_db = StoryDB(project_id)
    outline = await story_db.get_outline()
    if not outline:
        raise HTTPException(status_code=400, detail="请先生成大纲")

    # 优先读取用户自定义弧线占位符
    arc_plan = await story_db.get_arc_plan(str(arc_number))
    if arc_plan and arc_plan.get("is_placeholder"):
        return {
            "name": arc_plan.get("arc_theme", ""),
            "description": arc_plan.get("arc_goal", ""),
            "chapter_range": arc_plan.get("chapter_range", [1, 10]),
            "key_directions": arc_plan.get("key_directions", [])
        }

    # 回退到大纲三幕结构
    three_act = outline.get("three_act_structure", {})
    act_keys = ["act1", "act2a", "act2b", "act3"]
    return three_act.get(act_keys[min(arc_number - 1, len(act_keys) - 1)], {})


@app.post("/api/projects/{project_id}/generate/arc")
async def generate_arc(project_id: str, arc_number: int = 1, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    触发弧线规划生成 —— 非流式接口。

    执行流程：
        1. 读取 ProjectMeta、Bible、人物数据、已有伏笔。
        2. 通过 _resolve_arc_act_data() 获取 act_data（支持用户自定义弧线）。
        3. 实例化 ArcPlanner，生成宏观弧线规划（含情绪弧线、里程碑、转折点等）。
        4. 结果持久化到 arcs/arc_{arc_number}.json。

    Args:
        project_id: 项目 UUID。
        arc_number: 弧线序号，默认 1。
        temperature: LLM 采样温度。

    Returns:
        {"success": True, "message": "...", "data": {...}}。

    Raises:
        HTTPException: 400/404，前置条件不满足；500，生成失败。
    """
    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)
        project_meta = ProjectMeta(**meta)

        bible_db = BibleDB(project_id)
        bible_data = await bible_db.load("bible")
        if not bible_data:
            raise HTTPException(status_code=400, detail="请先生成 Bible")

        # 读取人物数据
        character_db = CharacterDB(project_id)
        characters_data = {"protagonist": {}, "supporting_characters": []}
        char_files = list((workspace_path / "characters").glob("*.json"))
        for char_file in char_files:
            if char_file.name != "relationships.json":
                async with aiofiles.open(char_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                    char_data = json.loads(content)
                if char_data.get("is_protagonist", False):
                    characters_data["protagonist"] = char_data
                else:
                    characters_data["supporting_characters"].append(char_data)

        # 兼容旧数据：若未找到标记为主角的卡片，将第一个配角提升为主角
        if not characters_data["protagonist"] and characters_data["supporting_characters"]:
            characters_data["protagonist"] = characters_data["supporting_characters"].pop(0)

        # 读取已有伏笔
        foreshadowing_db = ForeshadowingDB(project_id)
        existing_foreshadowing = await foreshadowing_db.list_all_foreshadowing()

        act_data = await _resolve_arc_act_data(project_id, arc_number)

        planner = ArcPlanner(project_id)
        result = await planner.generate(
            arc_number=arc_number,
            act_data=act_data,
            bible_data=bible_data,
            characters_data=characters_data,
            existing_foreshadowing=existing_foreshadowing,
            temperature=temperature,
            max_tokens=_config.GENERATOR_MAX_TOKENS,
            total_timeout=900,
            sock_read_timeout=120
        )

        return {
            "success": True,
            "message": f"弧线 {arc_number} 规划生成成功",
            "data": result
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成弧线规划失败: {str(e)}")


@app.post("/api/projects/{project_id}/stream/arc")
async def stream_generate_arc(project_id: str, request: Request, arc_number: int = 1, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    流式生成弧线规划（SSE 接口）。

    事件类型与 stream_generate_bible 相同：start / progress / token / complete / error / done。
    相比非流式接口，前端可实时观察 ArcPlanner 的 LLM 输出过程。

    Args:
        project_id: 项目 UUID。
        request: FastAPI Request 对象。
        arc_number: 弧线序号。
        temperature: LLM 采样温度。

    Returns:
        EventSourceResponse，SSE 事件流。
    """
    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    async def event_generator():
        try:
            async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
                content = await f.read()
                meta = json.loads(content)
            project_meta = ProjectMeta(**meta)

            bible_db = BibleDB(project_id)
            bible_data = await bible_db.load("bible")
            if not bible_data:
                yield {"event": "error", "data": json.dumps({"error": "请先生成 Bible"}, ensure_ascii=False)}
                return

            character_db = CharacterDB(project_id)
            characters_data = {"protagonist": {}, "supporting_characters": []}
            char_files = list((workspace_path / "characters").glob("*.json"))
            for char_file in char_files:
                if char_file.name != "relationships.json":
                    async with aiofiles.open(char_file, "r", encoding="utf-8") as f:
                        content = await f.read()
                        char_data = json.loads(content)
                    if char_data.get("is_protagonist", False):
                        characters_data["protagonist"] = char_data
                    else:
                        characters_data["supporting_characters"].append(char_data)

            # 兼容旧数据：若未找到标记为主角的卡片，将第一个配角提升为主角
            if not characters_data["protagonist"] and characters_data["supporting_characters"]:
                characters_data["protagonist"] = characters_data["supporting_characters"].pop(0)

            foreshadowing_db = ForeshadowingDB(project_id)
            existing_foreshadowing = await foreshadowing_db.list_all_foreshadowing()

            act_data = await _resolve_arc_act_data(project_id, arc_number)

            generator = ArcPlanner(project_id)

            async for event in generator.generate_stream(
                arc_number=arc_number,
                act_data=act_data,
                bible_data=bible_data,
                characters_data=characters_data,
                existing_foreshadowing=existing_foreshadowing,
                temperature=temperature,
                max_tokens=_config.GENERATOR_MAX_TOKENS,
                total_timeout=900,
                sock_read_timeout=120
            ):
                event_type = event.get("type", "message")
                if event_type == "start":
                    yield {
                        "event": "start",
                        "data": json.dumps({
                            "message": event.get("message", ""),
                            "prompt_length": event.get("prompt_length", 0),
                            "model": event.get("model", ""),
                            "role": event.get("role", ""),
                            "max_tokens": event.get("max_tokens", 0),
                            "temperature": event.get("temperature", 0),
                        }, ensure_ascii=False)
                    }
                elif event_type == "progress":
                    yield {"event": "progress", "data": json.dumps({"message": event.get("message", "")}, ensure_ascii=False)}
                elif event_type == "token":
                    yield {"event": "token", "data": json.dumps({"content": event.get("content", "")}, ensure_ascii=False)}
                elif event_type == "complete":
                    yield {"event": "complete", "data": json.dumps({"message": event.get("message", ""), "data": event.get("data", {})}, ensure_ascii=False)}
                elif event_type == "error":
                    yield {"event": "error", "data": json.dumps({"error": event.get("error", "未知错误")}, ensure_ascii=False)}

            yield {"event": "done", "data": json.dumps({"message": "流式传输完成"})}

        except Exception as e:
            logger.error(f"流式生成弧线规划失败: {e}")
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}
            return

    return EventSourceResponse(event_generator(), ping=15)


@app.post("/api/projects/{project_id}/generate/chapter")
async def generate_chapter_plan(project_id: str, chapter_number: int = 1, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    触发单章的章节规划生成。

    执行流程：
        1. 通过 StoryDB.get_arc_plan_for_chapter() 定位包含该章节的弧线规划。
        2. 读取上一章的完稿摘要（如存在），作为上下文衔接输入。
        3. 实例化 ChapterPlanner，生成场景序列（含意图、视角、人物、情绪、伏笔等）。
        4. 结果持久化到 chapters/chapter_{N}_plan.json。

    Args:
        project_id: 项目 UUID。
        chapter_number: 章节编号。
        temperature: LLM 采样温度。

    Returns:
        {"success": True, "message": "...", "data": {...}}。

    Raises:
        HTTPException: 400，未找到对应弧线规划；500，生成失败。
    """
    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        story_db = StoryDB(project_id)

        # 定位包含该章节的弧线规划
        arc_plan = await story_db.get_arc_plan_for_chapter(chapter_number)
        if not arc_plan:
            raise HTTPException(status_code=400, detail=f"未找到第 {chapter_number} 章的弧线规划，请先生成弧线规划")

        # 读取上一章摘要，用于上下文衔接
        previous_summary = ""
        if chapter_number > 1:
            prev_final = await story_db.get_chapter_final(chapter_number - 1)
            if prev_final:
                previous_summary = prev_final.get("summary", "")

        planner = ChapterPlanner(project_id)
        result = await planner.generate(
            chapter_number=chapter_number,
            arc_plan=arc_plan,
            previous_chapter_summary=previous_summary,
            temperature=temperature
        )

        return {
            "success": True,
            "message": f"第 {chapter_number} 章规划生成成功",
            "data": result
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成章节规划失败: {str(e)}")


@app.post("/api/projects/{project_id}/stream/chapter")
async def stream_generate_chapter_plan(project_id: str, request: Request, chapter_number: int = 1, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    流式生成章节规划（SSE 接口）。

    事件类型与 stream_generate_bible 相同：start / progress / token / complete / error / done。
    相比非流式接口，前端可实时观察 ChapterPlanner 的 LLM 输出过程。

    Args:
        project_id: 项目 UUID。
        request: FastAPI Request 对象。
        chapter_number: 章节编号。
        temperature: LLM 采样温度。

    Returns:
        EventSourceResponse，SSE 事件流。
    """
    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    async def event_generator():
        try:
            story_db = StoryDB(project_id)

            # 定位包含该章节的弧线规划
            arc_plan = await story_db.get_arc_plan_for_chapter(chapter_number)
            if not arc_plan:
                yield {"event": "error", "data": json.dumps({"error": f"未找到第 {chapter_number} 章的弧线规划，请先生成弧线规划"}, ensure_ascii=False)}
                return

            # 读取上一章摘要，用于上下文衔接
            previous_summary = ""
            if chapter_number > 1:
                prev_final = await story_db.get_chapter_final(chapter_number - 1)
                if prev_final:
                    previous_summary = prev_final.get("summary", "")

            generator = ChapterPlanner(project_id)

            async for event in generator.generate_stream(
                chapter_number=chapter_number,
                arc_plan=arc_plan,
                previous_chapter_summary=previous_summary,
                temperature=temperature
            ):
                event_type = event.get("type", "message")
                if event_type == "start":
                    yield {
                        "event": "start",
                        "data": json.dumps({
                            "message": event.get("message", ""),
                            "prompt_length": event.get("prompt_length", 0),
                            "model": event.get("model", ""),
                            "role": event.get("role", ""),
                            "max_tokens": event.get("max_tokens", 0),
                            "temperature": event.get("temperature", 0),
                        }, ensure_ascii=False)
                    }
                elif event_type == "progress":
                    yield {"event": "progress", "data": json.dumps({"message": event.get("message", "")}, ensure_ascii=False)}
                elif event_type == "token":
                    yield {"event": "token", "data": json.dumps({"content": event.get("content", "")}, ensure_ascii=False)}
                elif event_type == "complete":
                    yield {"event": "complete", "data": json.dumps({"message": event.get("message", ""), "data": event.get("data", {})}, ensure_ascii=False)}
                elif event_type == "error":
                    yield {"event": "error", "data": json.dumps({"error": event.get("error", "未知错误")}, ensure_ascii=False)}

            yield {"event": "done", "data": json.dumps({"message": "流式传输完成"})}

        except Exception as e:
            logger.error(f"流式生成章节规划失败: {e}")
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}
            return

    return EventSourceResponse(event_generator(), ping=15)


# ============================================================
# Issue Pool 与更新记录接口
# ============================================================

@app.get("/api/projects/{project_id}/issues")
async def get_issues(project_id: str):
    """
    聚合项目的 Issue Pool。

    Issue 来源：
        1. 未解决的伏笔：遍历 ForeshadowingDB，status != "resolved" 的项。
        2. 隐式问题：扫描 chapters/chapter_*_updates.json，提取 implicit_issues。
        3. 转折点检查：将大纲中的转折点转为 pending 状态的 issue，提醒作者关注。

    Args:
        project_id: 项目 UUID。

    Returns:
        {"project_id": "...", "issues": [...], "total": N}。

    Raises:
        HTTPException: 404，项目不存在；500，聚合失败。
    """
    try:
        workspace_path = _validate_project_path(project_id)
        if not workspace_path.exists():
            raise HTTPException(status_code=404, detail="项目不存在")

        foreshadowing_db = ForeshadowingDB(project_id)
        foreshadowing_items = await foreshadowing_db.list_all_foreshadowing()

        story_db = StoryDB(project_id)

        issues = []

        # 收集未解决的伏笔
        for item in foreshadowing_items:
            if item.get("status") != "resolved":
                issues.append({
                    "type": "foreshadowing",
                    "id": item.get("id", ""),
                    "description": item.get("description", ""),
                    "status": item.get("status", "active"),
                    "urgency": item.get("urgency", "medium")
                })

        # 从更新记录中提取 implicit_issues
        chapters_dir = workspace_path / "chapters"
        if chapters_dir.exists():
            for update_file in chapters_dir.glob("chapter_*_updates.json"):
                try:
                    async with aiofiles.open(update_file, "r", encoding="utf-8") as f:
                        content = await f.read()
                        records = json.loads(content)
                    if isinstance(records, list):
                        for record in records:
                            implicit = record.get("implicit_issues", [])
                            for issue in implicit:
                                if issue:
                                    issues.append({
                                        "type": "implicit",
                                        "id": f"implicit_{update_file.stem}_{len(issues)}",
                                        "description": issue,
                                        "status": "open",
                                        "urgency": "medium"
                                    })
                except Exception as read_err:
                    logger.warning(f"读取更新记录失败 {update_file.name}: {read_err}")

        # 连续性问题：检查转折点是否有对应章节
        outline = await story_db.get_outline()
        if outline:
            for tp in outline.get("turning_points", []):
                issues.append({
                    "type": "turning_point",
                    "id": f"tp_{tp.get('chapter', 0)}",
                    "description": f"转折点: {tp.get('name', '')} (第{tp.get('chapter', '?')}章)",
                    "status": "pending",
                    "urgency": "major"
                })

        return {
            "project_id": project_id,
            "issues": issues,
            "total": len(issues)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取 Issue Pool 失败: {str(e)}")


@app.get("/api/projects/{project_id}/chapters/{chapter_num}/updates")
async def get_chapter_updates(project_id: str, chapter_num: int):
    """
    获取某章节的更新记录（异步更新通知接口）。

    场景：场景生成完成后，UpdateExtractor 异步分析文本并更新知识库。
    前端通过轮询此接口检查是否有新的 implicit_issues 产生。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。

    Returns:
        {
            "chapter_number": N,
            "updates_count": M,
            "latest_update": {...},
            "implicit_issues": [...],
            "has_new_issues": bool
        }。

    Raises:
        HTTPException: 404，项目不存在；500，读取失败。
    """
    try:
        workspace_path = _validate_project_path(project_id)
        if not workspace_path.exists():
            raise HTTPException(status_code=404, detail="项目不存在")

        record_path = workspace_path / "chapters" / f"chapter_{chapter_num}_updates.json"
        if not record_path.exists():
            return {
                "chapter_number": chapter_num,
                "updates": [],
                "implicit_issues": [],
                "has_new_issues": False
            }

        async with aiofiles.open(record_path, "r", encoding="utf-8") as f:
            records = json.loads(await f.read())

        if not isinstance(records, list):
            records = [records]

        latest = records[-1] if records else {}
        implicit_issues = latest.get("implicit_issues", [])

        return {
            "chapter_number": chapter_num,
            "updates_count": len(records),
            "latest_update": latest,
            "implicit_issues": implicit_issues,
            "has_new_issues": len(implicit_issues) > 0
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取更新记录失败: {str(e)}")


@app.get("/api/projects/{project_id}/stream/logs")
async def stream_logs(project_id: str, request: Request, level: str = "INFO"):
    """
    SSE 接口：流式推送系统日志到前端监控面板。

    实现机制：
        1. 创建 asyncio.Queue 作为日志缓冲区（最大 500 条，防内存膨胀）。
        2. 将队列注册到 sse_log_handler，使后端所有 logging 消息自动入队。
        3. 通过 EventSourceResponse 持续从队列消费日志，按过滤级别推送。
        4. 每秒发送一次 ping 心跳保活；检测客户端断开时自动清理队列。

    Args:
        project_id: 项目 UUID。
        request: FastAPI Request 对象，用于 is_disconnected() 检测。
        level: 最低日志级别（DEBUG/INFO/WARNING/ERROR），默认 INFO。

    Returns:
        EventSourceResponse，SSE 日志流。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    level_map = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
    min_level = level_map.get(level.upper(), 20)

    log_queue = asyncio.Queue(maxsize=500)
    sse_log_handler.add_queue(log_queue)

    async def event_generator():
        try:
            yield {
                "event": "start",
                "data": json.dumps({"message": "日志流已连接"}, ensure_ascii=False)
            }
            while True:
                # 检测客户端是否已断开连接（如关闭标签页）
                if await request.is_disconnected():
                    break

                try:
                    log_entry = await asyncio.wait_for(log_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # 心跳保活：防止中间代理（如 Nginx）超时断开 SSE
                    yield {"event": "ping", "data": "keepalive"}
                    continue

                if level_map.get(log_entry.get("level", "INFO"), 20) >= min_level:
                    yield {
                        "event": "log",
                        "data": json.dumps(log_entry, ensure_ascii=False)
                    }
        except Exception as e:
            logger.error(f"日志流异常: {e}")
        finally:
            # 客户端断开时必须移除队列引用，防止内存泄漏
            sse_log_handler.remove_queue(log_queue)

    return EventSourceResponse(event_generator())


# ============================================================
# 流式初始化接口（SSE 兼容）
# ============================================================

@app.post("/api/projects/{project_id}/stream/characters")
async def stream_generate_characters(project_id: str, request: Request, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    流式生成人物设定（SSE 接口）。

    事件类型：start / progress / token / complete / error / done。
    前置条件：项目必须已存在 Bible。

    Returns:
        EventSourceResponse，SSE 事件流。
    """
    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    async def event_generator():
        try:
            async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
                content = await f.read()
                meta = json.loads(content)
            project_meta = ProjectMeta(**meta)

            bible_db = BibleDB(project_id)
            bible_data = await bible_db.load("bible")
            if not bible_data:
                yield {"event": "error", "data": json.dumps({"error": "请先生成 Bible"}, ensure_ascii=False)}
                return

            generator = CharacterGenerator(project_id)

            async for event in generator.generate_stream(
                project_meta=project_meta,
                bible_data=bible_data,
                temperature=temperature
            ):
                event_type = event.get("type", "message")
                if event_type == "start":
                    yield {
                        "event": "start",
                        "data": json.dumps({
                            "message": event.get("message", ""),
                            "prompt_length": event.get("prompt_length", 0),
                            "model": event.get("model", ""),
                            "role": event.get("role", ""),
                            "max_tokens": event.get("max_tokens", 0),
                            "temperature": event.get("temperature", 0),
                        }, ensure_ascii=False)
                    }
                elif event_type == "progress":
                    yield {"event": "progress", "data": json.dumps({"message": event.get("message", "")}, ensure_ascii=False)}
                elif event_type == "token":
                    yield {"event": "token", "data": json.dumps({"content": event.get("content", "")}, ensure_ascii=False)}
                elif event_type == "complete":
                    yield {"event": "complete", "data": json.dumps({"message": event.get("message", ""), "data": event.get("data", {})}, ensure_ascii=False)}
                elif event_type == "error":
                    yield {"event": "error", "data": json.dumps({"error": event.get("error", "未知错误")}, ensure_ascii=False)}

            yield {"event": "done", "data": json.dumps({"message": "流式传输完成"})}

        except Exception as e:
            logger.error(f"流式生成人物设定失败: {e}")
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}
            return

    return EventSourceResponse(event_generator(), ping=15)


@app.post("/api/projects/{project_id}/stream/outline")
async def stream_generate_outline(project_id: str, request: Request, temperature: float = _config.GENERATOR_TEMPERATURE):
    """
    流式生成全局大纲（SSE 接口）。

    事件类型：start / progress / token / complete / error / done。
    前置条件：项目必须已存在 Bible 和人物设定。

    Returns:
        EventSourceResponse，SSE 事件流。
    """
    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    async def event_generator():
        try:
            async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
                content = await f.read()
                meta = json.loads(content)
            project_meta = ProjectMeta(**meta)

            bible_db = BibleDB(project_id)
            bible_data = await bible_db.load("bible")
            if not bible_data:
                yield {"event": "error", "data": json.dumps({"error": "请先生成 Bible"}, ensure_ascii=False)}
                return

            character_db = CharacterDB(project_id)
            characters_data = {"protagonist": {}, "supporting_characters": []}
            char_files = list((workspace_path / "characters").glob("*.json"))
            for char_file in char_files:
                if char_file.name != "relationships.json":
                    async with aiofiles.open(char_file, "r", encoding="utf-8") as f:
                        content = await f.read()
                        char_data = json.loads(content)
                    if char_data.get("is_protagonist", False):
                        characters_data["protagonist"] = char_data
                    else:
                        characters_data["supporting_characters"].append(char_data)

            # 兼容旧数据：若未找到标记为主角的卡片，将第一个配角提升为主角
            if not characters_data["protagonist"] and characters_data["supporting_characters"]:
                characters_data["protagonist"] = characters_data["supporting_characters"].pop(0)

            generator = OutlineGenerator(project_id)

            async for event in generator.generate_stream(
                project_meta=project_meta,
                bible_data=bible_data,
                characters_data=characters_data,
                temperature=temperature
            ):
                event_type = event.get("type", "message")
                if event_type == "start":
                    yield {
                        "event": "start",
                        "data": json.dumps({
                            "message": event.get("message", ""),
                            "prompt_length": event.get("prompt_length", 0),
                            "model": event.get("model", ""),
                            "role": event.get("role", ""),
                            "max_tokens": event.get("max_tokens", 0),
                            "temperature": event.get("temperature", 0),
                        }, ensure_ascii=False)
                    }
                elif event_type == "progress":
                    yield {"event": "progress", "data": json.dumps({"message": event.get("message", "")}, ensure_ascii=False)}
                elif event_type == "token":
                    yield {"event": "token", "data": json.dumps({"content": event.get("content", "")}, ensure_ascii=False)}
                elif event_type == "complete":
                    yield {"event": "complete", "data": json.dumps({"message": event.get("message", ""), "data": event.get("data", {})}, ensure_ascii=False)}
                elif event_type == "error":
                    yield {"event": "error", "data": json.dumps({"error": event.get("error", "未知错误")}, ensure_ascii=False)}

            yield {"event": "done", "data": json.dumps({"message": "流式传输完成"})}

        except Exception as e:
            logger.error(f"流式生成大纲失败: {e}")
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}
            return

    return EventSourceResponse(event_generator(), ping=15)


# ============================================================
# 写作接口
# ============================================================

@app.get("/api/projects/{project_id}/chapters/{chapter_num}/plan")
async def get_chapter_plan(project_id: str, chapter_num: int):
    """
    获取章节规划。

    从 StoryDB 读取 chapter_{chapter_num}_plan.json，
    若返回的是 Pydantic ChapterPlan 对象，则调用 model_dump() 转为字典。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。

    Returns:
        章节规划字典，包含 title、chapter_goal、emotional_arc、scenes 列表等。

    Raises:
        HTTPException: 404，规划不存在；500，读取失败。
    """
    try:
        story_db = StoryDB(project_id)
        plan = await story_db.get_chapter_plan(chapter_num)

        if not plan:
            raise HTTPException(status_code=404, detail="章节规划不存在")

        # StoryDB 可能返回 Pydantic 模型或原始字典，统一处理
        if hasattr(plan, 'model_dump'):
            return plan.model_dump()
        return plan

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取章节规划失败: {str(e)}")


@app.get("/api/projects/{project_id}/chapters/{chapter_num}/draft")
async def get_chapter_draft(project_id: str, chapter_num: int):
    """
    获取章节草稿（包含已生成的各场景正文）。

    草稿结构：{"chapter_number": N, "scenes": [{"scene_index": 0, "text": "..."}, ...]}。
    若草稿不存在返回 404，前端据此判断是否需要重新生成。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。

    Returns:
        草稿字典。

    Raises:
        HTTPException: 404，草稿不存在；500，读取失败。
    """
    try:
        story_db = StoryDB(project_id)
        draft = await story_db.get_chapter_draft(chapter_num)

        if not draft:
            raise HTTPException(status_code=404, detail="章节草稿不存在")

        return draft

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取章节草稿失败: {str(e)}")


@app.get("/api/projects/{project_id}/chapters/{chapter_num}/scenes/{scene_index}/context")
async def get_scene_context(project_id: str, chapter_num: int, scene_index: int):
    """
    获取场景的注入上下文摘要（上下文探针 API）。

    用于前端"探针"按钮，展示 Writer 在生成该场景时实际接收到的上下文信息，
    帮助作者理解 AI 的"视野"并发现潜在的信息缺失。

    返回字段：
        scene_intent: 场景意图
        chapter_goal: 本章目标
        previous_text_preview: 前文尾部预览（截断至 200 字符）
        present_characters: 出场人物摘要（姓名、位置、情绪）
        relevant_world_rules_count: 注入的世界规则数量
        active_foreshadowing_count: 活跃伏笔数量
        style_reference_preview: 风格参考预览（截断）
        total_tokens_used / token_budget_remaining: Token 使用与剩余预算
        similar_scenes_count: 相似场景参考数量

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。
        scene_index: 场景序号（从 0 开始）。

    Returns:
        上下文摘要字典。

    Raises:
        HTTPException: 404，规划或场景不存在；500，构建失败。
    """
    try:
        story_db = StoryDB(project_id)
        chapter_plan_raw = await story_db.get_chapter_plan(chapter_num)
        if not chapter_plan_raw:
            raise HTTPException(status_code=404, detail="章节规划不存在")

        # 统一转换为字典
        if hasattr(chapter_plan_raw, 'model_dump'):
            chapter_plan_data = chapter_plan_raw.model_dump()
        else:
            chapter_plan_data = chapter_plan_raw

        chapter_plan = ChapterPlan(**chapter_plan_data)

        # 在场景列表中定位目标场景
        scene_plan_data = None
        for scene in chapter_plan_data.get("scenes", []):
            if scene.get("scene_index") == scene_index:
                scene_plan_data = scene
                break

        if not scene_plan_data:
            raise HTTPException(status_code=404, detail="场景不存在")

        scene_plan = ScenePlan(**scene_plan_data)

        # 通过 InjectionEngine 构建实际注入的上下文
        injection_engine = InjectionEngine(project_id)
        ctx = await injection_engine.build_context(
            scene_plan=scene_plan, chapter_plan=chapter_plan
        )

        # 返回摘要而非完整文本，避免响应体过大
        return {
            "scene_index": scene_index,
            "chapter_number": chapter_num,
            "scene_intent": ctx.scene_plan.intent,
            "chapter_goal": ctx.chapter_goal,
            "previous_text_preview": (
                ctx.previous_text[:200] + "..."
                if len(ctx.previous_text) > 200 else ctx.previous_text
            ),
            "present_characters": [
                {
                    "name": c.name,
                    "current_location": c.current_location,
                    "current_emotion": c.current_emotion
                }
                for c in ctx.present_character_cards
            ],
            "relevant_world_rules_count": len(ctx.relevant_world_rules),
            "active_foreshadowing_count": len(ctx.active_foreshadowing),
            "style_reference_preview": (
                ctx.style_reference[:200] + "..."
                if len(ctx.style_reference) > 200 else ctx.style_reference
            ),
            "total_tokens_used": ctx.total_tokens_used,
            "token_budget_remaining": ctx.token_budget_remaining,
            "similar_scenes_count": len(ctx.similar_scenes_reference)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"获取上下文失败: {str(e)}"
        )


@app.post("/api/projects/{project_id}/chapters/{chapter_num}/scenes/{scene_index}/write")
async def write_scene(project_id: str, chapter_num: int, scene_index: int):
    """
    触发单场景生成任务 —— 创建任务记录。

    当前为简化实现：直接返回 task_id，前端随后通过 SSE 接口 (/stream/...) 连接流式输出。
    可扩展为异步任务队列模式，将任务状态存入 Redis / 内存字典供查询。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。
        scene_index: 场景序号。

    Returns:
        {"success": True, "task_id": "...", "message": "写作任务已创建"}。

    Raises:
        HTTPException: 500，任务创建失败。
    """
    try:
        task_id = f"{project_id}_{chapter_num}_{scene_index}_{int(datetime.now().timestamp())}"

        return {
            "success": True,
            "task_id": task_id,
            "message": "写作任务已创建"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建写作任务失败: {str(e)}")


@app.post("/api/projects/{project_id}/stream/{chapter_num}/{scene_index}")
async def stream_scene(project_id: str, chapter_num: int, scene_index: int, request: Request):
    """
    SSE 接口：流式写作单场景。

    核心流程：
        1. 读取章节规划（ChapterPlan）与目标场景规划（ScenePlan）。
        2. 实例化 Writer，调用 write_scene_stream() 获取异步 token 生成器。
        3. 每收到一个 token 立即通过 SSE 推送（打字机效果）。
        4. 每累积 50 个 token 推送一次进度事件（减少网络开销）。
        5. 生成完成后推送 scene_complete 事件（含字数统计）。
        6. 最后推送 done 事件标志流结束。

    SSE 事件类型说明：
        start          —— 携带场景意图、目标字数、视角人物等元信息
        token          —— 单个文本片段（可能为中文单字或英文单词）
        progress       —— 阶段性进度（token_count、char_count）
        scene_complete —— 场景生成完毕，携带最终字数
        done           —— 流正常结束
        error          —— 异常终止

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。
        scene_index: 场景序号（从 0 开始）。
        request: FastAPI Request 对象，用于读取请求体中的 temperature。

    Returns:
        EventSourceResponse，SSE 文本流。

    Raises:
        HTTPException: 404，项目不存在；流内返回 error 事件。
    """
    body = await request.json()
    temperature = body.get("temperature", _config.WRITER_TEMPERATURE)

    workspace_path = _validate_project_path(project_id)

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    async def event_generator():
        try:
            # 读取章节规划
            story_db = StoryDB(project_id)
            chapter_plan_raw = await story_db.get_chapter_plan(chapter_num)

            if not chapter_plan_raw:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "章节规划不存在，请先生成章节规划"}, ensure_ascii=False)
                }
                return

            # 统一转为字典（兼容 Pydantic 模型或原始字典）
            if hasattr(chapter_plan_raw, 'model_dump'):
                chapter_plan_data = chapter_plan_raw.model_dump()
            else:
                chapter_plan_data = chapter_plan_raw

            chapter_plan = ChapterPlan(**chapter_plan_data)

            # 在场景列表中定位目标场景
            scene_plan_data = None
            for scene in chapter_plan_data.get("scenes", []):
                if scene.get("scene_index") == scene_index:
                    scene_plan_data = scene
                    break

            if not scene_plan_data:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": f"场景 {scene_index} 不存在"}, ensure_ascii=False)
                }
                return

            scene_plan = ScenePlan(**scene_plan_data)

            # 推送开始事件，携带场景元信息
            yield {
                "event": "start",
                "data": json.dumps({
                    "scene_index": scene_index,
                    "intent": scene_plan.intent,
                    "target_word_count": scene_plan.target_word_count,
                    "pov_character": scene_plan.pov_character
                }, ensure_ascii=False)
            }

            # 创建 Writer 实例并启动流式生成
            writer = Writer(project_id)

            full_text = ""
            token_count = 0

            async for token in writer.write_scene_stream(
                scene_plan=scene_plan,
                chapter_plan=chapter_plan,
                temperature=temperature
            ):
                full_text += token
                token_count += 1

                # 每 50 个 token 推送一次进度事件
                if token_count % 50 == 0:
                    yield {
                        "event": "progress",
                        "data": json.dumps({
                            "token_count": token_count,
                            "char_count": len(full_text)
                        }, ensure_ascii=False)
                    }

                # 推送文本片段（打字机效果的核心）
                yield {
                    "event": "token",
                    "data": json.dumps({"content": token}, ensure_ascii=False)
                }

            # 推送场景完成事件
            yield {
                "event": "scene_complete",
                "data": json.dumps({
                    "scene_index": scene_index,
                    "word_count": len(full_text),
                    "token_count": token_count
                }, ensure_ascii=False)
            }

            # 推送流结束标志
            yield {
                "event": "done",
                "data": json.dumps({"message": "流式传输完成"}, ensure_ascii=False)
            }

        except Exception as e:
            logger.error(f"流式写作失败: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}, ensure_ascii=False)
            }
            return

    return EventSourceResponse(
        event_generator(),
        ping=15,  # 每 15 秒发送一次 ping，防止代理超时断开
        ping_message_factory=lambda: {"event": "ping", "data": "keepalive"}
    )


@app.post("/api/projects/{project_id}/chapters/{chapter_num}/confirm")
async def confirm_chapter(project_id: str, chapter_num: int):
    """
    用户确认章节完稿。

    完稿流程：
        1. 读取章节草稿，合并所有场景的 text 为完整章节正文。
        2. 生成章节摘要（当前为简化版，仅统计场景数与字数）。
        3. 持久化到 story/chapter_{N}_final.json（ChapterFinal 结构）。
        4. 生成只读 TXT/Markdown 发行版到 exports/ 目录，附带元信息头与只读声明。
        5. 更新 project_meta.json 的 current_chapter 字段。
        6. 将所有场景同步到向量库（collection="chapter_scenes"），确保语义检索覆盖完稿内容。

    安全提示：导出文件头部明确标注"只读发行版"，防止用户误修改后期望后端同步。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。

    Returns:
        {"success": True, "message": "章节已确认", "word_count": N}。

    Raises:
        HTTPException: 404，草稿不存在；500，处理失败。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        story_db = StoryDB(project_id)

        draft = await story_db.get_chapter_draft(chapter_num)

        if not draft or "scenes" not in draft:
            raise HTTPException(status_code=404, detail="章节草稿不存在")

        # 合并所有场景正文为完整章节
        scenes = draft["scenes"]
        full_text = "\n\n".join(scene.get("text", "") for scene in scenes)

        # 生成章节摘要（简化版）
        summary = f"第{chapter_num}章，共{len(scenes)}个场景，约{len(full_text)}字"

        from core.schemas import ChapterFinal
        chapter_final_obj = ChapterFinal(
            chapter_number=chapter_num,
            title=draft.get("title", f"第{chapter_num}章"),
            full_text=full_text,
            word_count=len(full_text),
            scene_texts=[scene.get("text", "") for scene in scenes],
            summary=summary,
            confirmed_at=datetime.now().isoformat()
        )

        await story_db.save_chapter_final(chapter_final_obj)

        # 生成只读发行版 Markdown 文件
        exports_dir = workspace_path / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        meta_info = f"""# {chapter_final_obj.title}

> 第 {chapter_num} 章 | {len(scenes)} 个场景 | 约 {len(full_text)} 字
> 确认时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 本文件由 MANS 系统自动生成，任何手动修改均不会被后端同步。
> 如需修改正文，请在前端写作界面中使用「编辑」或「重写」功能。

---

"""
        export_text = meta_info + full_text

        # 清理文件名中的非法字符，确保跨平台可用
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', chapter_final_obj.title)
        export_filename = f"第{chapter_num}章_{safe_title}.md"
        export_path = exports_dir / export_filename

        async with aiofiles.open(export_path, 'w', encoding='utf-8') as f:
            await f.write(export_text)

        # 更新项目当前章节进度
        meta_path = workspace_path / "project_meta.json"

        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)

        meta["current_chapter"] = max(meta.get("current_chapter", 0), chapter_num)

        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))

        # 将所有场景同步到向量库，供后续语义检索
        for scene in scenes:
            await _sync_scene_to_vector_store(
                project_id=project_id,
                chapter_num=chapter_num,
                scene_index=scene.get("scene_index", 0),
                text=scene.get("text", ""),
                emotional_tone=scene.get("emotional_tone", ""),
                pov_character=scene.get("pov_character", "")
            )

        return {
            "success": True,
            "message": "章节已确认",
            "word_count": len(full_text)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"确认章节失败: {str(e)}")


@app.get("/api/projects/{project_id}/exports")
async def list_exports(project_id: str):
    """
    列出项目所有已导出的完稿文件。

    扫描 workspace/{project_id}/exports/ 目录，返回 .md 和 .txt 文件列表，
    包含文件名、自动提取的章节号、文件大小（字节）、修改时间。

    Args:
        project_id: 项目 UUID。

    Returns:
        {"project_id": "...", "exports": [...]}。
    """
    workspace_path = _validate_project_path(project_id)
    exports_dir = workspace_path / "exports"
    if not exports_dir.exists():
        return {"project_id": project_id, "exports": []}

    exports = []
    for file_path in sorted(exports_dir.iterdir()):
        if file_path.is_file() and file_path.suffix in (".md", ".txt"):
            exports.append({
                "filename": file_path.name,
                "chapter_number": _extract_chapter_number(file_path.name),
                "size": file_path.stat().st_size,
                "modified_at": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
            })
    return {"project_id": project_id, "exports": exports}


def _extract_chapter_number(filename: str) -> int | None:
    """
    从导出文件名中提取章节号。

    匹配模式：文件名中包含"第{N}章"，如"第5章_宗门试炼.md"→5。

    Args:
        filename: 导出文件名。

    Returns:
        章节号整数，若无法提取则返回 None。
    """
    match = re.search(r"第(\d+)章", filename)
    return int(match.group(1)) if match else None


@app.get("/api/projects/{project_id}/exports/{filename}")
async def get_export(project_id: str, filename: str):
    """
    获取单个导出文件的内容。

    安全校验：通过 resolve() + relative_to() 确保请求文件严格位于 exports/ 目录内，
    防止通过 "../" 等路径遍历读取其他项目或系统文件。

    Args:
        project_id: 项目 UUID。
        filename: 导出文件名（需 URL 编码）。

    Returns:
        {"filename": "...", "content": "...", "size": N, "is_readonly": True}。

    Raises:
        HTTPException: 403，路径非法；404，文件不存在；500，读取失败。
    """
    workspace_path = _validate_project_path(project_id)
    exports_dir = workspace_path / "exports"
    file_path = exports_dir / filename

    # 路径安全校验
    try:
        file_path.resolve().relative_to(exports_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="非法路径")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")

    try:
        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
            content = await f.read()
        return {
            "filename": filename,
            "content": content,
            "size": len(content),
            "is_readonly": True
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取导出文件失败: {e}")


async def _sync_scene_to_vector_store(
    project_id: str,
    chapter_num: int,
    scene_index: int,
    text: str,
    emotional_tone: str = "",
    pov_character: str = ""
) -> None:
    """
    将单个场景同步到向量库（内部辅助函数）。

    用途：
        1. 场景生成完成后立即同步，确保语义检索覆盖最新内容。
        2. 章节完稿时批量同步，保证向量库与最终定稿一致。
        3. 用户手动编辑后同步，确保修改可被后续场景检索到。

    数据写入 collection="chapter_scenes"，metadata 包含章节号、场景号、
    情绪基调、视角人物、更新时间等维度信息，供多条件过滤检索。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。
        scene_index: 场景序号。
        text: 场景完整正文。
        emotional_tone: 情绪基调，可选。
        pov_character: 视角人物，可选。

    Note:
        此函数为 fire-and-forget 风格内部调用，异常仅记录日志不抛出，
        避免向量库故障阻塞主业务流程。
    """
    try:
        vector_store = VectorStore(project_id)
        await vector_store.upsert(
            collection="chapter_scenes",
            id=f"ch{chapter_num}_sc{scene_index}",
            text=text,
            metadata={
                "chapter": chapter_num,
                "scene": scene_index,
                "emotional_tone": emotional_tone,
                "pov_character": pov_character,
                "updated_at": datetime.now().isoformat()
            }
        )
    except Exception as e:
        logger.error(f"同步场景到向量库失败 ch{chapter_num}_sc{scene_index}: {e}")


@app.put("/api/projects/{project_id}/chapters/{chapter_num}/scenes/{scene_index}")
async def edit_scene(project_id: str, chapter_num: int, scene_index: int, content: dict):
    """
    用户手动编辑某场景内容。

    执行流程：
        1. 读取现有草稿，定位目标场景。
        2. 更新场景 text，标记 edited_by_user=True 与 edited_at 时间戳。
        3. 保存修改后的草稿。
        4. 同步到向量库，确保语义检索反映最新内容。
        5. 异步触发 UpdateExtractor（不阻塞响应），从修改后的文本中提取状态变化并更新知识库。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。
        scene_index: 场景序号。
        content: 请求体字典，必须包含 "text" 字段。

    Returns:
        {"success": True, "message": "场景已更新"}。

    Raises:
        HTTPException: 404，草稿不存在；500，保存失败。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        story_db = StoryDB(project_id)

        draft = await story_db.get_chapter_draft(chapter_num)

        if not draft:
            raise HTTPException(status_code=404, detail="章节草稿不存在")

        # 更新目标场景内容并标记用户编辑
        scenes = draft.get("scenes", [])
        for scene in scenes:
            if scene.get("scene_index") == scene_index:
                scene["text"] = content.get("text", scene.get("text", ""))
                scene["edited_at"] = datetime.now().isoformat()
                scene["edited_by_user"] = True
                break

        await story_db.save_chapter_draft(chapter_num, draft)

        # 同步到向量库
        updated_scene = next(
            (s for s in scenes if s.get("scene_index") == scene_index), None
        )
        if updated_scene:
            await _sync_scene_to_vector_store(
                project_id=project_id,
                chapter_num=chapter_num,
                scene_index=scene_index,
                text=updated_scene.get("text", ""),
                emotional_tone=updated_scene.get("emotional_tone", ""),
                pov_character=updated_scene.get("pov_character", "")
            )

            # 异步触发状态提取（fire-and-forget，不阻塞 HTTP 响应）
            try:
                from core.schemas import ScenePlan
                extractor = UpdateExtractor(project_id)
                asyncio.create_task(
                    extractor.extract_and_update(
                        generated_text=updated_scene.get("text", ""),
                        chapter_number=chapter_num,
                        scene_index=scene_index,
                        scene_plan=ScenePlan(
                            scene_index=scene_index,
                            intent=updated_scene.get("intent", ""),
                            pov_character=updated_scene.get("pov_character", ""),
                            present_characters=updated_scene.get("present_characters", []),
                            emotional_tone=updated_scene.get("emotional_tone", ""),
                            target_word_count=updated_scene.get("target_word_count", 1200)
                        ),
                        sync=False
                    )
                )
            except Exception as update_err:
                logger.warning(f"手动编辑后异步更新提取失败: {update_err}")

        return {"success": True, "message": "场景已更新"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"编辑场景失败: {str(e)}")


@app.post("/api/projects/{project_id}/chapters/{chapter_num}/scenes/{scene_index}/extract")
async def manual_extract_scene(project_id: str, chapter_num: int, scene_index: int):
    """
    手动触发场景文本的状态提取与知识库同步。

    使用场景：用户对某场景进行了大幅手动修改，希望主动触发 UpdateExtractor
    分析新文本并同步人物状态、伏笔进度等，而不等待自动保存的异步触发。

    执行流程：
        1. 读取当前草稿获取场景文本。
        2. 读取章节规划获取 ScenePlan（提供人物列表等上下文）。
        3. 调用 UpdateExtractor.extract_and_update() 异步执行。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。
        scene_index: 场景序号。

    Returns:
        {"success": True, "message": "已触发知识库同步，后台正在分析文本..."}。

    Raises:
        HTTPException: 404，草稿或场景不存在；500，触发失败。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        story_db = StoryDB(project_id)

        # 读取当前草稿
        draft = await story_db.get_chapter_draft(chapter_num)
        if not draft:
            raise HTTPException(status_code=404, detail="章节草稿不存在")

        scenes = draft.get("scenes", [])
        scene_data = None
        for s in scenes:
            if s.get("scene_index") == scene_index:
                scene_data = s
                break

        if not scene_data:
            raise HTTPException(status_code=404, detail="场景不存在")

        # 读取章节规划以获取 ScenePlan
        chapter_plan_raw = await story_db.get_chapter_plan(chapter_num)
        if not chapter_plan_raw:
            raise HTTPException(status_code=404, detail="章节规划不存在")

        chapter_plan_data = (
            chapter_plan_raw.model_dump()
            if hasattr(chapter_plan_raw, 'model_dump')
            else chapter_plan_raw
        )

        scene_plan_data = None
        for scene in chapter_plan_data.get("scenes", []):
            if scene.get("scene_index") == scene_index:
                scene_plan_data = scene
                break

        if not scene_plan_data:
            raise HTTPException(status_code=404, detail="场景规划不存在")

        scene_plan = ScenePlan(**scene_plan_data)

        # 异步触发提取（不阻塞响应）
        extractor = UpdateExtractor(project_id)
        asyncio.create_task(
            extractor.extract_and_update(
                generated_text=scene_data.get("text", ""),
                chapter_number=chapter_num,
                scene_index=scene_index,
                scene_plan=scene_plan,
                sync=False
            )
        )

        return {
            "success": True,
            "message": "已触发知识库同步，后台正在分析文本..."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"手动提取失败: {str(e)}")


@app.post("/api/projects/{project_id}/chapters/{chapter_num}/scenes/{scene_index}/rollback")
async def rollback_scene_updates_endpoint(
    project_id: str, chapter_num: int, scene_index: int
):
    """
    回滚指定场景产生的知识库更新。

    使用场景：用户对某场景不满意，决定重新生成。在重新生成前调用此接口，
    可清理该场景上次生成时引入的人物状态变化、世界规则新增、伏笔状态变更等，
    防止旧更新与新内容冲突。

    回滚范围（由 UpdateExtractor.rollback_scene_updates() 实现）：
        - 人物状态历史：删除该场景产生的 state_history 快照，重建当前状态。
        - 世界规则：移除该场景新增的规则。
        - 伏笔：回退状态变更，删除该场景新埋设的伏笔。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。
        scene_index: 场景序号。

    Returns:
        {"success": True, "message": "...", "details": {...}}，
        message 为人类可读的中文回滚摘要。

    Raises:
        HTTPException: 500，回滚失败。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        extractor = UpdateExtractor(project_id)
        result = await extractor.rollback_scene_updates(
            chapter_number=chapter_num,
            scene_index=scene_index
        )

        # 构建人类可读的中文回滚摘要
        message_parts = []
        if result.get("characters_rolled_back", 0) > 0:
            message_parts.append(f"回滚 {result['characters_rolled_back']} 个人物状态")
        if result.get("rules_removed", 0) > 0:
            message_parts.append(f"移除 {result['rules_removed']} 条世界规则")
        if result.get("foreshadowing_reverted", 0) > 0:
            message_parts.append(f"回退 {result['foreshadowing_reverted']} 个伏笔状态")
        if result.get("foreshadowing_removed", 0) > 0:
            message_parts.append(f"移除 {result['foreshadowing_removed']} 个新伏笔")

        msg = result.get("message", "")
        if message_parts:
            msg = "；".join(message_parts)
        elif not msg:
            msg = "该场景无知识库更新记录，无需回滚"

        return {
            "success": True,
            "message": msg,
            "details": result
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回滚失败: {str(e)}")


@app.post("/api/projects/{project_id}/chapters/{chapter_num}/scenes/{scene_index}/regenerate")
async def regenerate_scene_stream_endpoint(
    project_id: str, chapter_num: int, scene_index: int, request: Request
):
    """
    根据用户反馈流式重新生成场景（SSE 接口）。

    与 stream_scene 的区别：
        1. 接收用户 feedback（如"节奏太快，增加环境描写"）。
        2. 获取当前场景的已有草稿作为 previous_attempt 传入 Writer，
           使 LLM 知道之前写了什么以及需要改进的方向。
        3. 调用 writer.regenerate_scene_stream() 而非 write_scene_stream()。

    事件类型与 stream_scene 完全一致：start / token / progress / scene_complete / done / error。

    Args:
        project_id: 项目 UUID。
        chapter_num: 章节编号。
        scene_index: 场景序号。
        request: FastAPI Request 对象，用于读取 feedback 与 temperature。

    Returns:
        EventSourceResponse，SSE 文本流。

    Raises:
        HTTPException: 400，场景无草稿无法重写；404，规划或场景不存在；500，重写失败。
    """
    try:
        body = await request.json()
        feedback = body.get("feedback", "")
        temperature = body.get("temperature", _config.WRITER_TEMPERATURE)

        workspace_path = _validate_project_path(project_id)
        if not workspace_path.exists():
            raise HTTPException(status_code=404, detail="项目不存在")

        story_db = StoryDB(project_id)
        chapter_plan_raw = await story_db.get_chapter_plan(chapter_num)
        if not chapter_plan_raw:
            raise HTTPException(status_code=404, detail="章节规划不存在")

        if hasattr(chapter_plan_raw, 'model_dump'):
            chapter_plan_data = chapter_plan_raw.model_dump()
        else:
            chapter_plan_data = chapter_plan_raw

        chapter_plan = ChapterPlan(**chapter_plan_data)

        scene_plan_data = None
        for scene in chapter_plan_data.get("scenes", []):
            if scene.get("scene_index") == scene_index:
                scene_plan_data = scene
                break

        if not scene_plan_data:
            raise HTTPException(status_code=404, detail="场景不存在")

        scene_plan = ScenePlan(**scene_plan_data)

        # 获取当前草稿作为 previous_attempt
        draft = await story_db.get_chapter_draft(chapter_num)
        previous_attempt = ""
        if draft and "scenes" in draft:
            for scene in draft.get("scenes", []):
                if scene.get("scene_index") == scene_index:
                    previous_attempt = scene.get("text", "")
                    break

        if not previous_attempt:
            raise HTTPException(status_code=400, detail="场景暂无草稿，无法重写")

        async def event_generator():
            try:
                writer = Writer(project_id)
                full_text = ""
                token_count = 0

                yield {
                    "event": "start",
                    "data": json.dumps({
                        "scene_index": scene_index,
                        "intent": scene_plan.intent,
                        "feedback": feedback
                    }, ensure_ascii=False)
                }

                async for token in writer.regenerate_scene_stream(
                    scene_plan=scene_plan,
                    chapter_plan=chapter_plan,
                    previous_attempt=previous_attempt,
                    feedback=feedback,
                    temperature=temperature
                ):
                    full_text += token
                    token_count += 1

                    if token_count % 50 == 0:
                        yield {
                            "event": "progress",
                            "data": json.dumps({
                                "token_count": token_count,
                                "char_count": len(full_text)
                            }, ensure_ascii=False)
                        }

                    yield {
                        "event": "token",
                        "data": json.dumps({"content": token}, ensure_ascii=False)
                    }

                yield {
                    "event": "scene_complete",
                    "data": json.dumps({
                        "scene_index": scene_index,
                        "word_count": len(full_text),
                        "token_count": token_count
                    }, ensure_ascii=False)
                }

                yield {
                    "event": "done",
                    "data": json.dumps({"message": "流式传输完成"}, ensure_ascii=False)
                }

            except Exception as e:
                logger.error(f"流式重写失败: {e}")
                yield {
                    "event": "error",
                    "data": json.dumps({"message": str(e)}, ensure_ascii=False)
                }

        return EventSourceResponse(
            event_generator(),
            ping=15,
            ping_message_factory=lambda: {"event": "ping", "data": "keepalive"}
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重写场景失败: {str(e)}")


# ============================================================
# 向量库同步接口
# ============================================================

@app.post("/api/projects/{project_id}/sync/vectors")
async def sync_vectors(project_id: str):
    """
    手动全量同步项目数据到向量库。

    使用场景：用户直接修改了工作区中的 JSON 文件（如手动编辑 bible.json），
    导致向量库与文件系统不一致，通过此接口强制重建向量索引。

    同步范围：
        1. Bible 规则：读取 bible.json 中的 world_rules，写入 bible_rules collection。
        2. 人物卡片：扫描 characters/*.json，写入 character_cards collection。
        3. 章节场景：扫描 story/chapter_*_draft.json，写入 chapter_scenes collection。

    Args:
        project_id: 项目 UUID。

    Returns:
        {"success": True, "message": "向量库同步完成", "results": {"bible": N, "characters": M, "scenes": K}}。

    Raises:
        HTTPException: 404，项目不存在；500，同步失败。
    """
    workspace_path = _validate_project_path(project_id)
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        vector_store = VectorStore(project_id)
        results = {"bible": 0, "characters": 0, "scenes": 0}

        # 1. 同步 Bible 规则
        bible_db = BibleDB(project_id)
        bible_data = await bible_db.load("bible")
        if bible_data:
            items = []
            rules = bible_data.get("world_rules", [])
            if isinstance(rules, list):
                for i, rule in enumerate(rules):
                    content_text = rule.get("content", rule.get("description", ""))
                    category = rule.get("category", "special")
                    items.append({
                        "id": f"bible_rule_{i}",
                        "text": content_text,
                        "metadata": {"type": "world_rule", "category": category}
                    })
            if items:
                await vector_store.upsert_batch(collection="bible_rules", items=items)
                results["bible"] = len(items)

        # 2. 同步人物卡片
        character_db = CharacterDB(project_id)
        characters = await character_db.list_all_characters()
        if characters:
            items = []
            for char in characters:
                if not char or not isinstance(char, dict):
                    continue
                name = char.get("name", "未知")
                text_parts = [f"人物姓名：{name}"]
                if char.get("appearance"):
                    text_parts.append(f"外貌：{char.get('appearance', '')}")
                if char.get("personality_core"):
                    text_parts.append(f"性格：{char.get('personality_core', '')}")
                if char.get("background"):
                    text_parts.append(f"背景：{char.get('background', '')}")
                items.append({
                    "id": f"char_{char.get('id', name)}",
                    "text": "，".join(text_parts),
                    "metadata": {"type": "character", "name": name}
                })
            if items:
                await vector_store.upsert_batch(collection="character_cards", items=items)
                results["characters"] = len(items)

        # 3. 同步所有章节场景草稿
        story_db = StoryDB(project_id)
        scene_count = 0
        story_dir = workspace_path / "story"
        if story_dir.exists():
            for draft_file in story_dir.glob("chapter_*_draft.json"):
                try:
                    async with aiofiles.open(draft_file, "r", encoding="utf-8") as f:
                        content = await f.read()
                        draft = json.loads(content)
                    for scene in draft.get("scenes", []):
                        await _sync_scene_to_vector_store(
                            project_id=project_id,
                            chapter_num=draft.get("chapter_number", 0),
                            scene_index=scene.get("scene_index", 0),
                            text=scene.get("text", ""),
                            emotional_tone=scene.get("emotional_tone", ""),
                            pov_character=scene.get("pov_character", "")
                        )
                        scene_count += 1
                except Exception as e:
                    logger.warning(f"同步草稿文件失败 {draft_file.name}: {e}")
            results["scenes"] = scene_count

        return {
            "success": True,
            "message": "向量库同步完成",
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"同步向量库失败: {str(e)}")


# ============================================================
# 知识库查看接口
# ============================================================

@app.get("/api/projects/{project_id}/bible")
async def get_bible(project_id: str):
    """
    获取 Bible（世界观设定）。

    Returns:
        Bible 字典，若未生成则返回 {"error": "Bible 不存在", "message": "请先生成 Bible"}。

    Raises:
        HTTPException: 500，读取失败。
    """
    try:
        bible_db = BibleDB(project_id)
        bible = await bible_db.load("bible")
        if not bible:
            return {"error": "Bible 不存在", "message": "请先生成 Bible"}
        return bible
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取 Bible 失败: {str(e)}")


@app.get("/api/projects/{project_id}/characters")
async def get_characters(project_id: str):
    """
    获取项目下所有人物列表。

    Returns:
        {"characters": [...]}，每个人物为完整 CharacterCard 字典。

    Raises:
        HTTPException: 500，读取失败。
    """
    try:
        character_db = CharacterDB(project_id)
        characters = await character_db.list_all_characters()
        return {"characters": characters}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取人物列表失败: {str(e)}")


@app.get("/api/projects/{project_id}/characters/{char_id}")
async def get_character(project_id: str, char_id: str):
    """
    获取单个人物详情。

    Args:
        project_id: 项目 UUID。
        char_id: 人物 ID（或文件名标识）。

    Returns:
        人物完整字典。

    Raises:
        HTTPException: 404，人物不存在；500，读取失败。
    """
    try:
        character_db = CharacterDB(project_id)
        character = await character_db.get_character_by_id(char_id)

        if not character:
            raise HTTPException(status_code=404, detail="人物不存在")

        return character

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取人物失败: {str(e)}")


@app.get("/api/projects/{project_id}/foreshadowing")
async def get_foreshadowing(project_id: str):
    """
    获取项目下所有伏笔列表。

    Returns:
        {"foreshadowing": [...]}，包含每条伏笔的 description、status、urgency、
        planted_chapter、resolution_chapter 等字段。

    Raises:
        HTTPException: 500，读取失败。
    """
    try:
        foreshadowing_db = ForeshadowingDB(project_id)
        items = await foreshadowing_db.list_all_foreshadowing()
        return {"foreshadowing": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取伏笔列表失败: {str(e)}")


@app.get("/api/projects/{project_id}/outline")
async def get_outline(project_id: str):
    """
    获取全局大纲。

    Returns:
        大纲字典（three_act_structure、main_conflict、turning_points、foreshadowing_list 等），
        若未生成则返回空字典 {}。

    Raises:
        HTTPException: 500，读取失败。
    """
    try:
        story_db = StoryDB(project_id)
        outline = await story_db.get_outline()
        return outline if outline else {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取大纲失败: {str(e)}")


# ============================================================
# 静态文件挂载与首页
# ============================================================

@app.get("/api/config")
async def get_system_config():
    """
    获取系统运行时配置。

    返回各角色的默认温度、max_tokens、速率限制等配置信息，
    供前端初始化设置面板时使用。

    安全说明：返回结果中隐藏 API Key 等敏感信息。

    Returns:
        配置字典，包含 temperatures、max_tokens、rate_limit 等字段。
    """
    return {
        "temperatures": {
            "writer": _config.WRITER_TEMPERATURE,
            "generator": _config.GENERATOR_TEMPERATURE,
            "trim": _config.TRIM_TEMPERATURE,
            "extract": _config.EXTRACT_TEMPERATURE,
        },
        "max_tokens": {
            "writer": _config.WRITER_MAX_TOKENS,
            "generator": _config.GENERATOR_MAX_TOKENS,
            "trim": _config.TRIM_MAX_TOKENS,
            "extract": _config.EXTRACT_MAX_TOKENS,
        },
        "rate_limit": _config.RATE_LIMIT,
        "injection_token_budget": _config.INJECTION_TOKEN_BUDGET,
        "active_provider": _config.ACTIVE_PROVIDER,
    }


@app.get("/")
async def root():
    """
    单页应用入口。

    返回 frontend/index.html，由前端路由接管后续导航。
    """
    return FileResponse(Path(__file__).parent / "index.html")


# 挂载 frontend/ 目录为静态文件服务，使 /frontend/app.js、/frontend/styles.css 等可直接访问
frontend_dir = Path(__file__).parent
app.mount("/frontend", StaticFiles(directory=frontend_dir), name="frontend")
