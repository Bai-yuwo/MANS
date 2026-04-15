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
        (workspace_path / "vectors").mkdir(parents=True)
        
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
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(project_meta.model_dump(), f, ensure_ascii=False, indent=2)
        
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
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta = json.load(f)
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
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
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
            "has_bible": (workspace_path / "bible.json").exists(),
            "has_characters": (workspace_path / "characters").exists() and any(
                (workspace_path / "characters").glob("*.json")
            ),
            "has_outline": (workspace_path / "outline.json").exists(),
            "current_chapter": 0,
            "status": "unknown"
        }
        
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
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
async def generate_bible(project_id: str):
    """触发 Bible 生成（非流式，保留兼容）"""
    workspace_path = Path("workspace") / project_id
    
    if not workspace_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")
    
    try:
        # 读取项目元信息
        with open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            meta = json.load(f)
        
        project_meta = ProjectMeta(**meta)
        
        # 创建生成器并生成
        generator = BibleGenerator(project_id)
        
        # 使用进度回调
        progress_messages = []
        def progress_callback(msg: str):
            progress_messages.append(msg)
            logger.info(f"[BibleGenerator] {msg}")
        
        generator.set_progress_callback(progress_callback)
        result = await generator.generate(project_meta=project_meta)
        
        return {
            "success": True,
            "message": "Bible 生成成功",
            "data": result,
            "progress": progress_messages
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成 Bible 失败: {str(e)}")


@app.post("/api/projects/{project_id}/stream/bible")
async def stream_generate_bible(project_id: str, request: Request):
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
            with open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
                meta = json.load(f)
            
            project_meta = ProjectMeta(**meta)
            
            # 创建生成器
            generator = BibleGenerator(project_id)
            
            # 推送开始事件
            yield {
                "event": "start",
                "data": json.dumps({"message": "开始生成 Bible..."}, ensure_ascii=False)
            }
            
            # 使用流式生成，实时推送token
            async for event in generator.generate_stream(project_meta=project_meta):
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
        bible_db.save("bible", bible_data)
        return {"success": True, "message": "Bible 已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新 Bible 失败: {str(e)}")


@app.post("/api/projects/{project_id}/generate/characters")
async def generate_characters(project_id: str):
    """触发人物生成"""
    try:
        # 读取项目元信息
        workspace_path = Path("workspace") / project_id
        with open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            meta = json.load(f)
        project_meta = ProjectMeta(**meta)
        
        # 读取 Bible
        bible_db = BibleDB(project_id)
        bible_data = bible_db.load("bible")
        if not bible_data:
            raise HTTPException(status_code=400, detail="请先生成 Bible")
        
        # 生成人物
        generator = CharacterGenerator(project_id)
        result = await generator.generate(
            project_meta=project_meta,
            bible_data=bible_data
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
async def generate_outline(project_id: str):
    """触发大纲生成"""
    try:
        # 读取项目元信息
        workspace_path = Path("workspace") / project_id
        with open(workspace_path / "project_meta.json", "r", encoding="utf-8") as f:
            meta = json.load(f)
        project_meta = ProjectMeta(**meta)
        
        # 读取 Bible
        bible_db = BibleDB(project_id)
        bible_data = bible_db.load("bible")
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
                with open(char_file, "r", encoding="utf-8") as f:
                    char_data = json.load(f)
                if not characters_data["protagonist"]:
                    characters_data["protagonist"] = char_data
                else:
                    characters_data["supporting_characters"].append(char_data)
        
        # 生成大纲
        generator = OutlineGenerator(project_id)
        result = await generator.generate(
            project_meta=project_meta,
            bible_data=bible_data,
            characters_data=characters_data
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
        
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        
        meta["status"] = "writing"
        
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        
        return {"success": True, "message": "大纲已确认，进入写作阶段"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"确认大纲失败: {str(e)}")


# ============================================================
# 写作接口
# ============================================================

@app.get("/api/projects/{project_id}/chapters/{chapter_num}/plan")
async def get_chapter_plan(project_id: str, chapter_num: int):
    """获取章节规划"""
    try:
        story_db = StoryDB(project_id)
        plan = story_db.get_chapter_plan(chapter_num)
        
        if not plan:
            raise HTTPException(status_code=404, detail="章节规划不存在")
        
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
async def stream_scene(project_id: str, chapter_num: int, scene_index: int):
    """
    SSE 接口：流式接收生成内容
    
    实际生成过程在此接口中执行，通过 SSE 实时推送 token
    """
    async def event_generator():
        try:
            # 读取章节规划
            story_db = StoryDB(project_id)
            chapter_plan_data = story_db.get_chapter_plan(chapter_num)
            
            if not chapter_plan_data:
                yield f"data: {json.dumps({'type': 'error', 'data': {'message': '章节规划不存在'}})}\n\n"
                return
            
            # 构建 ChapterPlan 和 ScenePlan
            chapter_plan = ChapterPlan(**chapter_plan_data)
            
            scene_plan_data = None
            for scene in chapter_plan_data.get("scenes", []):
                if scene.get("scene_index") == scene_index:
                    scene_plan_data = scene
                    break
            
            if not scene_plan_data:
                yield f"data: {json.dumps({'type': 'error', 'data': {'message': '场景不存在'}})}\n\n"
                return
            
            scene_plan = ScenePlan(**scene_plan_data)
            
            # 发送开始事件
            yield f"data: {json.dumps({'type': 'scene_start', 'data': {'scene_index': scene_index, 'intent': scene_plan.intent}})}\n\n"
            
            # 创建 Writer 并生成
            writer = Writer(project_id)
            
            full_text = ""
            async for token in writer.write_scene_stream(
                scene_plan=scene_plan,
                chapter_plan=chapter_plan
            ):
                full_text += token
                # SSE 格式：data: {...}\n\n
                yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"
            
            # 发送完成事件
            yield f"data: {json.dumps({'type': 'scene_complete', 'data': {'scene_index': scene_index, 'word_count': len(full_text)}})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/api/projects/{project_id}/chapters/{chapter_num}/confirm")
async def confirm_chapter(project_id: str, chapter_num: int):
    """用户确认章节完稿"""
    try:
        story_db = StoryDB(project_id)
        
        # 合并所有场景为章节完稿
        draft = story_db.get_chapter_draft(chapter_num)
        
        if not draft or "scenes" not in draft:
            raise HTTPException(status_code=404, detail="章节草稿不存在")
        
        # 合并场景文本
        scenes = draft["scenes"]
        full_text = "\n\n".join(scene.get("text", "") for scene in scenes)
        
        # 生成摘要（简化处理，实际应调用小模型）
        summary = f"第{chapter_num}章，共{len(scenes)}个场景，约{len(full_text)}字"
        
        # 保存完稿
        chapter_final = {
            "chapter_number": chapter_num,
            "title": draft.get("title", f"第{chapter_num}章"),
            "full_text": full_text,
            "word_count": len(full_text),
            "scene_texts": [scene.get("text", "") for scene in scenes],
            "summary": summary,
            "confirmed_at": datetime.now().isoformat()
        }
        
        story_db.save_chapter_final(chapter_num, chapter_final)
        
        # 更新项目当前章节
        workspace_path = Path("workspace") / project_id
        meta_path = workspace_path / "project_meta.json"
        
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        
        meta["current_chapter"] = max(meta.get("current_chapter", 0), chapter_num)
        
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        
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
        draft = story_db.get_chapter_draft(chapter_num)
        
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
        story_db.save_chapter_draft(chapter_num, draft)
        
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
        bible = bible_db.load("bible")
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
        characters = character_db.list_all_characters()
        return {"characters": characters}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取人物列表失败: {str(e)}")


@app.get("/api/projects/{project_id}/characters/{char_id}")
async def get_character(project_id: str, char_id: str):
    """获取单个人物详情"""
    try:
        character_db = CharacterDB(project_id)
        character = character_db.get_character_by_id(char_id)
        
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
        items = foreshadowing_db.list_all_foreshadowing()
        return {"foreshadowing": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取伏笔列表失败: {str(e)}")


@app.get("/api/projects/{project_id}/outline")
async def get_outline(project_id: str):
    """获取大纲"""
    try:
        story_db = StoryDB(project_id)
        outline = story_db.get_outline()
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
from pathlib import Path
frontend_dir = Path(__file__).parent
app.mount("/frontend", StaticFiles(directory=frontend_dir), name="frontend")
