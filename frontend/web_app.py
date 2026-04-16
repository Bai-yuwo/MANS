"""
frontend/web_app.py
FastAPI Web 应用主文件

职责：
1. 提供 RESTful API 接口
2. 支持 SSE 流式输出
3. 挂载静态文件（前端页面）
4. 处理项目全生命周期管理

API 设计遵循文档第7章规范
"""

import asyncio
import json
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

# 导入核心模块
from core.schemas import (
    ProjectMeta, ScenePlan, ChapterPlan,
    CharacterCard, WorldRule, ForeshadowingItem
)
from core.config import get_config
from core.logging_config import get_logger, log_exception

logger = get_logger('frontend.web_app')

# 导入生成器
from generators import (
    BibleGenerator, CharacterGenerator, OutlineGenerator,
    ArcPlanner, ChapterPlanner
)

# 导入 Writer
from writer import Writer

# 导入知识库
from knowledge_bases.bible_db import BibleDB
from knowledge_bases.character_db import CharacterDB
from knowledge_bases.story_db import StoryDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB
from knowledge_bases.style_db import StyleDB

app = FastAPI(title="MANS - Multi-Agent Novel System")

# ============================================================
# Pydantic 请求/响应模型
# ============================================================

class CreateProjectRequest(BaseModel):
    """创建项目请求"""
    name: str
    genre: str = "玄幻"
    core_idea: str
    protagonist_seed: str
    target_length: str = "中篇(10-50万)"
    tone: str = ""
    style_reference: str = ""
    forbidden_elements: list[str] = []


class GenerateResponse(BaseModel):
    """生成操作响应"""
    success: bool
    message: str
    data: Optional[dict] = None


class ProjectStatusResponse(BaseModel):
    """项目状态响应"""
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
    创建新项目
    
    创建 workspace/{project_id}/ 目录结构
    保存 project_meta.json
    """
    project_id = str(uuid.uuid4())
    workspace_path = Path("workspace") / project_id
    
    try:
        # 创建目录结构
        (workspace_path / "characters").mkdir(parents=True)
        (workspace_path / "chapters").mkdir(parents=True)
        (workspace_path / "arcs").mkdir(parents=True)
        # vector_store 目录由 VectorStore 类自动创建，无需手动创建
        
        # 创建 ProjectMeta
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
        
        # 保存项目元信息
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
    """获取项目列表"""
    workspace_path = Path("workspace")
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
    """获取项目详情"""
    meta_path = Path("workspace") / project_id / "project_meta.json"
    
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
    """删除项目"""
    import shutil
    
    project_path = Path("workspace") / project_id
    
    if not project_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")
    
    try:
        shutil.rmtree(project_path)
        return {"success": True, "message": "项目已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除项目失败: {str(e)}")


@app.get("/api/projects/{project_id}/status")
async def get_project_status(project_id: str):
    """获取项目初始化/写作状态"""
    workspace_path = Path("workspace") / project_id
    
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
async def generate_bible(project_id: str, temperature: float = 0.7):
    """触发 Bible 生成（非流式，保留兼容）"""
    workspace_path = Path("workspace") / project_id

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        # 读取项目元信息
        async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)

        project_meta = ProjectMeta(**meta)

        # 创建生成器并生成
        generator = BibleGenerator(project_id)

        # 使用进度回调
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
async def stream_generate_bible(project_id: str, request: Request, temperature: float = 0.7):
    """
    流式生成 Bible（SSE）

    实时推送生成进度和LLM输出
    """
    workspace_path = Path("workspace") / project_id

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    async def event_generator():
        """SSE事件生成器"""
        try:
            # 读取项目元信息
            async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
                content = await f.read()
                meta = json.loads(content)

            project_meta = ProjectMeta(**meta)

            # 创建生成器
            generator = BibleGenerator(project_id)

            # 推送开始事件
            yield {
                "event": "start",
                "data": json.dumps({"message": "开始生成 Bible..."}, ensure_ascii=False)
            }

            # 使用流式生成，实时推送token
            async for event in generator.generate_stream(project_meta=project_meta, temperature=temperature):
                event_type = event.get("type", "message")
                
                if event_type == "progress":
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
            
            # 推送done事件
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
    
    return EventSourceResponse(event_generator())


@app.post("/api/projects/{project_id}/confirm/bible")
async def confirm_bible(project_id: str):
    """用户确认 Bible"""
    # 这里可以添加确认逻辑，如版本标记
    return {"success": True, "message": "Bible 已确认"}


@app.put("/api/projects/{project_id}/bible")
async def update_bible(project_id: str, bible_data: dict):
    """用户修改 Bible 内容"""
    try:
        bible_db = BibleDB(project_id)
        # 修复：传入key参数 "bible"
        await bible_db.save("bible", bible_data)
        return {"success": True, "message": "Bible 已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新 Bible 失败: {str(e)}")


@app.post("/api/projects/{project_id}/generate/characters")
async def generate_characters(project_id: str, temperature: float = 0.7):
    """触发人物生成"""
    try:
        # 读取项目元信息
        workspace_path = Path("workspace") / project_id
        async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)
        project_meta = ProjectMeta(**meta)

        # 读取 Bible
        bible_db = BibleDB(project_id)
        bible_data = await bible_db.load("bible")
        if not bible_data:
            raise HTTPException(status_code=400, detail="请先生成 Bible")

        # 生成人物
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成人物失败: {str(e)}")


@app.post("/api/projects/{project_id}/confirm/characters")
async def confirm_characters(project_id: str):
    """用户确认人物"""
    return {"success": True, "message": "人物设定已确认"}


@app.post("/api/projects/{project_id}/generate/outline")
async def generate_outline(project_id: str, temperature: float = 0.7):
    """触发大纲生成"""
    try:
        # 读取项目元信息
        workspace_path = Path("workspace") / project_id
        async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)
        project_meta = ProjectMeta(**meta)

        # 读取 Bible
        bible_db = BibleDB(project_id)
        bible_data = await bible_db.load("bible")
        if not bible_data:
            raise HTTPException(status_code=400, detail="请先生成 Bible")

        # 读取人物
        character_db = CharacterDB(project_id)
        characters_data = {
            "protagonist": {},
            "supporting_characters": []
        }

        # 构建人物数据（简化处理）
        char_files = list((workspace_path / "characters").glob("*.json"))
        for char_file in char_files:
            if char_file.name != "relationships.json":
                async with aiofiles.open(char_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                    char_data = json.loads(content)
                if not characters_data["protagonist"]:
                    characters_data["protagonist"] = char_data
                else:
                    characters_data["supporting_characters"].append(char_data)

        # 生成大纲
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
    """用户确认大纲"""
    # 更新项目状态为可写作
    try:
        workspace_path = Path("workspace") / project_id
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


@app.post("/api/projects/{project_id}/generate/arc")
async def generate_arc(project_id: str, arc_number: int = 1, temperature: float = 0.7):
    """触发弧线规划生成"""
    workspace_path = Path("workspace") / project_id

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        async with aiofiles.open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)
        project_meta = ProjectMeta(**meta)

        # 读取 Bible
        bible_db = BibleDB(project_id)
        bible_data = await bible_db.load("bible")
        if not bible_data:
            raise HTTPException(status_code=400, detail="请先生成 Bible")

        # 读取人物
        character_db = CharacterDB(project_id)
        characters_data = {"protagonist": {}, "supporting_characters": []}
        char_files = list((workspace_path / "characters").glob("*.json"))
        for char_file in char_files:
            if char_file.name != "relationships.json":
                async with aiofiles.open(char_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                    char_data = json.loads(content)
                if not characters_data["protagonist"]:
                    characters_data["protagonist"] = char_data
                else:
                    characters_data["supporting_characters"].append(char_data)

        # 读取大纲
        story_db = StoryDB(project_id)
        outline = await story_db.get_outline()
        if not outline:
            raise HTTPException(status_code=400, detail="请先生成大纲")

        # 获取对应幕的数据
        three_act = outline.get("three_act_structure", {})
        act_keys = ["act1", "act2a", "act2b", "act3"]
        act_data = three_act.get(act_keys[min(arc_number - 1, len(act_keys) - 1)], {})

        # 读取已有伏笔
        foreshadowing_db = ForeshadowingDB(project_id)
        existing_foreshadowing = await foreshadowing_db.list_all_foreshadowing()

        # 生成弧线规划
        planner = ArcPlanner(project_id)
        result = await planner.generate(
            arc_number=arc_number,
            act_data=act_data,
            bible_data=bible_data,
            characters_data=characters_data,
            existing_foreshadowing=existing_foreshadowing,
            temperature=temperature
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


@app.post("/api/projects/{project_id}/generate/chapter")
async def generate_chapter_plan(project_id: str, chapter_number: int = 1, temperature: float = 0.7):
    """触发章节规划生成"""
    workspace_path = Path("workspace") / project_id

    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        story_db = StoryDB(project_id)

        # 读取弧线规划（找到包含该章节的弧线）
        arc_plan = await story_db.get_arc_plan_for_chapter(chapter_number)
        if not arc_plan:
            raise HTTPException(status_code=400, detail=f"未找到第 {chapter_number} 章的弧线规划，请先生成弧线规划")

        # 读取上一章摘要
        previous_summary = ""
        if chapter_number > 1:
            prev_final = await story_db.get_chapter_final(chapter_number - 1)
            if prev_final:
                previous_summary = prev_final.get("summary", "")

        # 生成章节规划
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


@app.get("/api/projects/{project_id}/issues")
async def get_issues(project_id: str):
    """获取 Issue Pool"""
    try:
        workspace_path = Path("workspace") / project_id
        if not workspace_path.exists():
            raise HTTPException(status_code=404, detail="项目不存在")
        
        foreshadowing_db = ForeshadowingDB(project_id)
        foreshadowing_items = await foreshadowing_db.list_all_foreshadowing()
        
        story_db = StoryDB(project_id)
        
        # 收集各类 issue
        issues = []
        
        # 未解决的伏笔
        for item in foreshadowing_items:
            if item.get("status") != "resolved":
                issues.append({
                    "type": "foreshadowing",
                    "id": item.get("id", ""),
                    "description": item.get("description", ""),
                    "status": item.get("status", "active"),
                    "urgency": item.get("urgency", "medium")
                })
        
        # 连续性问题（简化：检查章节间状态一致性）
        outline = await story_db.get_outline()
        if outline:
            # 检查转折点是否有对应章节
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


@app.post("/api/projects/{project_id}/stream/characters")
async def stream_generate_characters(project_id: str, request: Request, temperature: float = 0.7):
    """流式生成人物设定（SSE）"""
    workspace_path = Path("workspace") / project_id

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

            yield {"event": "start", "data": json.dumps({"message": "开始生成人物设定..."}, ensure_ascii=False)}

            async for event in generator.generate_stream(
                project_meta=project_meta,
                bible_data=bible_data,
                temperature=temperature
            ):
                event_type = event.get("type", "message")
                if event_type == "progress":
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

    return EventSourceResponse(event_generator())


@app.post("/api/projects/{project_id}/stream/outline")
async def stream_generate_outline(project_id: str, request: Request, temperature: float = 0.7):
    """流式生成大纲（SSE）"""
    workspace_path = Path("workspace") / project_id

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
                    if not characters_data["protagonist"]:
                        characters_data["protagonist"] = char_data
                    else:
                        characters_data["supporting_characters"].append(char_data)

            generator = OutlineGenerator(project_id)

            yield {"event": "start", "data": json.dumps({"message": "开始生成大纲..."}, ensure_ascii=False)}

            async for event in generator.generate_stream(
                project_meta=project_meta,
                bible_data=bible_data,
                characters_data=characters_data,
                temperature=temperature
            ):
                event_type = event.get("type", "message")
                if event_type == "progress":
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

    return EventSourceResponse(event_generator())


# ============================================================
# 写作接口
# ============================================================

@app.get("/api/projects/{project_id}/chapters/{chapter_num}/plan")
async def get_chapter_plan(project_id: str, chapter_num: int):
    """获取章节规划"""
    try:
        story_db = StoryDB(project_id)
        plan = await story_db.get_chapter_plan(chapter_num)
        
        if not plan:
            raise HTTPException(status_code=404, detail="章节规划不存在")
        
        # ChapterPlan 对象需要转换为 dict
        if hasattr(plan, 'model_dump'):
            return plan.model_dump()
        return plan
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取章节规划失败: {str(e)}")


@app.post("/api/projects/{project_id}/chapters/{chapter_num}/scenes/{scene_index}/write")
async def write_scene(project_id: str, chapter_num: int, scene_index: int):
    """
    触发单场景生成
    
    返回 task_id，用于 SSE 流式接收
    """
    try:
        # 生成任务ID
        task_id = f"{project_id}_{chapter_num}_{scene_index}_{int(datetime.now().timestamp())}"
        
        # 这里可以存储任务状态，供 SSE 接口查询
        # 简化实现：直接返回 task_id，SSE 接口直接使用参数
        
        return {
            "success": True,
            "task_id": task_id,
            "message": "写作任务已创建"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建写作任务失败: {str(e)}")


@app.get("/api/projects/{project_id}/stream/{chapter_num}/{scene_index}")
async def stream_scene(project_id: str, chapter_num: int, scene_index: int, request: Request, temperature: float = 0.75):
    """
    SSE 接口：流式写作场景
    
    使用 EventSourceResponse 提供规范的 SSE 流式输出，
    支持打字机效果和实时进度推送。
    
    事件类型：
    - start: 开始生成
    - token: 文本片段（打字机效果）
    - progress: 进度信息
    - scene_complete: 场景完成
    - done: 流结束
    - error: 错误信息
    """
    workspace_path = Path("workspace") / project_id
    
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")
    
    async def event_generator():
        try:
            # 读取章节规划
            story_db = StoryDB(project_id)
            chapter_plan_data = await story_db.get_chapter_plan(chapter_num)
            
            if not chapter_plan_data:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "章节规划不存在，请先生成章节规划"}, ensure_ascii=False)
                }
                return
            
            # 构建 ChapterPlan
            chapter_plan = ChapterPlan(**chapter_plan_data)
            
            # 查找对应场景
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
            
            # 发送开始事件
            yield {
                "event": "start",
                "data": json.dumps({
                    "scene_index": scene_index,
                    "intent": scene_plan.intent,
                    "target_word_count": scene_plan.target_word_count,
                    "pov_character": scene_plan.pov_character
                }, ensure_ascii=False)
            }
            
            # 创建 Writer 并流式生成
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
                
                # 每50个token发送一次进度（减少网络开销）
                if token_count % 50 == 0:
                    yield {
                        "event": "progress",
                        "data": json.dumps({
                            "token_count": token_count,
                            "char_count": len(full_text)
                        }, ensure_ascii=False)
                    }
                
                # 发送文本片段（打字机效果）
                yield {
                    "event": "token",
                    "data": json.dumps({"content": token}, ensure_ascii=False)
                }
            
            # 发送完成事件
            yield {
                "event": "scene_complete",
                "data": json.dumps({
                    "scene_index": scene_index,
                    "word_count": len(full_text),
                    "token_count": token_count
                }, ensure_ascii=False)
            }
            
            # 发送结束事件
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
    
    return EventSourceResponse(
        event_generator(),
        ping=15,  # 每15秒发送ping保持连接
        ping_message_factory=lambda: {"event": "ping", "data": "keepalive"}
    )


@app.post("/api/projects/{project_id}/chapters/{chapter_num}/confirm")
async def confirm_chapter(project_id: str, chapter_num: int):
    """用户确认章节完稿"""
    try:
        story_db = StoryDB(project_id)
        
        # 合并所有场景为章节完稿
        draft = await story_db.get_chapter_draft(chapter_num)
        
        if not draft or "scenes" not in draft:
            raise HTTPException(status_code=404, detail="章节草稿不存在")
        
        # 合并场景文本
        scenes = draft["scenes"]
        full_text = "\n\n".join(scene.get("text", "") for scene in scenes)
        
        # 生成摘要（简化处理，实际应调用小模型）
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
        
        # 更新项目当前章节
        workspace_path = Path("workspace") / project_id
        meta_path = workspace_path / "project_meta.json"
        
        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            content = await f.read()
            meta = json.loads(content)
        
        meta["current_chapter"] = max(meta.get("current_chapter", 0), chapter_num)
        
        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))
        
        return {
            "success": True,
            "message": "章节已确认",
            "word_count": len(full_text)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"确认章节失败: {str(e)}")


@app.put("/api/projects/{project_id}/chapters/{chapter_num}/scenes/{scene_index}")
async def edit_scene(project_id: str, chapter_num: int, scene_index: int, content: dict):
    """用户手动编辑某场景内容"""
    try:
        story_db = StoryDB(project_id)
        
        # 获取现有草稿
        draft = await story_db.get_chapter_draft(chapter_num)
        
        if not draft:
            raise HTTPException(status_code=404, detail="章节草稿不存在")
        
        # 更新场景内容
        scenes = draft.get("scenes", [])
        for scene in scenes:
            if scene.get("scene_index") == scene_index:
                scene["text"] = content.get("text", scene.get("text", ""))
                scene["edited_at"] = datetime.now().isoformat()
                scene["edited_by_user"] = True
                break
        
        # 保存
        await story_db.save_chapter_draft(chapter_num, draft)
        
        return {"success": True, "message": "场景已更新"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"编辑场景失败: {str(e)}")


# ============================================================
# 知识库查看接口
# ============================================================

@app.get("/api/projects/{project_id}/bible")
async def get_bible(project_id: str):
    """获取 Bible"""
    try:
        bible_db = BibleDB(project_id)
        # 修复：传入key参数 "bible"
        bible = await bible_db.load("bible")
        if not bible:
            return {"error": "Bible 不存在", "message": "请先生成 Bible"}
        return bible
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取 Bible 失败: {str(e)}")


@app.get("/api/projects/{project_id}/characters")
async def get_characters(project_id: str):
    """获取人物列表"""
    try:
        character_db = CharacterDB(project_id)
        characters = await character_db.list_all_characters()
        return {"characters": characters}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取人物列表失败: {str(e)}")


@app.get("/api/projects/{project_id}/characters/{char_id}")
async def get_character(project_id: str, char_id: str):
    """获取单个人物详情"""
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
    """获取伏笔列表"""
    try:
        foreshadowing_db = ForeshadowingDB(project_id)
        items = await foreshadowing_db.list_all_foreshadowing()
        return {"foreshadowing": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取伏笔列表失败: {str(e)}")


@app.get("/api/projects/{project_id}/outline")
async def get_outline(project_id: str):
    """获取大纲"""
    try:
        story_db = StoryDB(project_id)
        outline = await story_db.get_outline()
        return outline if outline else {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取大纲失败: {str(e)}")


# ============================================================
# 静态文件挂载
# ============================================================

@app.get("/")
async def root():
    """首页"""
    return FileResponse("frontend/index.html")


# 挂载静态文件
frontend_dir = Path(__file__).parent
app.mount("/frontend", StaticFiles(directory=frontend_dir), name="frontend")
