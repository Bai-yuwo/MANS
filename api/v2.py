"""
api/v2.py

17-Agent 架构的新 API 路由(P3)。

路由列表:
    POST /api/v2/projects              — 创建项目(新架构目录结构)
    GET  /api/v2/projects              — 列出项目
    GET  /api/v2/projects/{pid}        — 读取项目元信息
    DELETE /api/v2/projects/{pid}      — 删除项目
    POST /api/v2/projects/{pid}/run    — 启动 Director(user_prompt)
    GET  /api/v2/projects/{pid}/stream — SSE 消费 StreamPacket
    POST /api/v2/projects/{pid}/respond — 用户确认/回复(续接 Director)
    GET  /api/v2/projects/{pid}/status — 查询项目当前 stage 与确认状态

SSE 事件格式:
    event: reasoning   data: {"content": "思考摘要...", "agent": "Director"}
    event: output      data: {"content": "正文token...", "agent": "Writer"}
    event: completed   data: {"res_id": "...", "total_tokens": 123, "tool_calls": []}
    event: confirm     data: {"from_stage": "INIT", "to_stage": "PLAN", "summary": "...", "prompt": "..."}
    event: error       data: {"error": "..."}
"""

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from core.config import get_config
from core.logging_config import get_logger
from core.stream_packet import CompletedPayload, ConfirmPayload, StreamPacket

from api.session_manager import get_session_manager
from knowledge_bases.bible_db import BibleDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB
from knowledge_bases.character_db import CharacterDB
from knowledge_bases.story_db import StoryDB

logger = get_logger("api.v2")

router = APIRouter(prefix="/api/v2")

# --------------------------------------------------------
# 配置与路径
# --------------------------------------------------------
_cfg = get_config()
_WORKSPACE_ROOT = Path(_cfg.WORKSPACE_PATH).resolve()


def _project_path(project_id: str) -> Path:
    """解析并校验项目路径,防止路径遍历。"""
    p = (_WORKSPACE_ROOT / project_id).resolve()
    try:
        p.relative_to(_WORKSPACE_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="非法项目ID")
    return p


# --------------------------------------------------------
# Pydantic 模型
# --------------------------------------------------------
class CreateProjectRequest(BaseModel):
    name: str
    genre: str = "玄幻"
    core_idea: str = ""
    protagonist_seed: str = ""
    target_length: str = "中篇(10-50万)"
    tone: str = ""
    style_reference: str = ""
    forbidden_elements: list[str] = []


class RunRequest(BaseModel):
    user_prompt: str = Field(..., description="给 Director 的 user prompt")


class RespondRequest(BaseModel):
    reply: str = Field(..., description="用户回复,如'同意,进入下一阶段'")


class CommandRequest(BaseModel):
    instruction: str = Field(..., description="用户给 Director 的任意指令,如'补充一个反派角色'或'跳过当前阶段'")


# --------------------------------------------------------
# 项目管理
# --------------------------------------------------------
@router.post("/projects")
async def create_project_v2(request: CreateProjectRequest):
    """
    创建新项目(新架构目录结构)。

    目录结构与 CLAUDE.md 一致:
        workspace/{pid}/
        ├── project_meta.json
        ├── bible.json
        ├── foreshadowing.json
        ├── outline.json
        ├── style_config.json
        ├── characters/
        ├── arcs/
        ├── chapters/
        │   ├── scene_beatsheets/
        │   ├── chapter_{n}_draft.json
        │   └── chapter_{n}_final.json
        ├── runs/
        └── vector_store/
    """
    project_id = str(uuid.uuid4())
    proj_dir = _project_path(project_id)

    try:
        # 创建目录骨架
        (proj_dir / "characters").mkdir(parents=True, exist_ok=True)
        (proj_dir / "arcs").mkdir(parents=True, exist_ok=True)
        (proj_dir / "chapters" / "scene_beatsheets").mkdir(parents=True, exist_ok=True)
        (proj_dir / "runs").mkdir(parents=True, exist_ok=True)
        (proj_dir / "vector_store").mkdir(parents=True, exist_ok=True)

        # 创建初始空 KB 文件
        empty_json = json.dumps({"items": []}, ensure_ascii=False, indent=2)
        for fname in ["bible.json", "foreshadowing.json", "outline.json", "style_config.json"]:
            async with aiofiles.open(proj_dir / fname, "w", encoding="utf-8") as f:
                await f.write(empty_json)

        # project_meta.json
        meta = {
            "id": project_id,
            "name": request.name,
            "genre": request.genre,
            "core_idea": request.core_idea,
            "protagonist_seed": request.protagonist_seed,
            "target_length": request.target_length,
            "tone": request.tone,
            "style_reference": request.style_reference,
            "forbidden_elements": request.forbidden_elements,
            "stage": "INIT",
            "status": "active",
            "current_chapter": 0,
            "created_at": datetime.now().isoformat(),
        }
        async with aiofiles.open(proj_dir / "project_meta.json", "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))

        logger.info(f"项目创建: {project_id} ({request.name})")
        return {"success": True, "project_id": project_id, "message": "项目创建成功"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("创建项目失败")
        raise HTTPException(status_code=500, detail=f"创建项目失败: {e}")


@router.get("/projects")
async def list_projects_v2():
    """列出所有项目(读取 project_meta.json 摘要)。"""
    projects = []
    if _WORKSPACE_ROOT.exists():
        for d in sorted(_WORKSPACE_ROOT.iterdir()):
            if d.is_dir():
                meta_path = d / "project_meta.json"
                if meta_path.exists():
                    try:
                        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
                            meta = json.loads(await f.read())
                        projects.append({
                            "id": meta.get("id", d.name),
                            "name": meta.get("name", "未命名"),
                            "genre": meta.get("genre", ""),
                            "stage": meta.get("stage", "unknown"),
                            "status": meta.get("status", ""),
                            "current_chapter": meta.get("current_chapter", 0),
                        })
                    except Exception:
                        pass
    return {"projects": projects}


@router.get("/projects/{project_id}")
async def get_project_v2(project_id: str):
    """获取单个项目完整元信息。"""
    meta_path = _project_path(project_id) / "project_meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            return json.loads(await f.read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取失败: {e}")


@router.delete("/projects/{project_id}")
async def delete_project_v2(project_id: str):
    """删除项目(不可恢复)。"""
    proj_dir = _project_path(project_id)
    if not proj_dir.exists():
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        shutil.rmtree(proj_dir)
        # 同时清理会话
        await get_session_manager().remove(project_id)
        return {"success": True, "message": "项目已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")


@router.get("/projects/{project_id}/status")
async def get_project_status_v2(project_id: str):
    """查询项目当前 stage 与 Orchestrator 确认状态。"""
    proj_dir = _project_path(project_id)
    meta_path = proj_dir / "project_meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
        meta = json.loads(await f.read())

    sess = await get_session_manager().get(project_id)
    return {
        "project_id": project_id,
        "stage": meta.get("stage", "unknown"),
        "status": meta.get("status", "unknown"),
        "current_chapter": meta.get("current_chapter", 0),
        "session_active": sess is not None,
        "pump_running": sess.is_pump_running if sess else False,
        "waiting_confirm": sess.is_waiting_confirm if sess else False,
        "confirm_payload": (
            sess.orch.confirm_payload.model_dump() if sess and sess.orch.confirm_payload else None
        ),
    }


@router.get("/projects/{project_id}/overview")
async def get_project_overview(project_id: str):
    """
    项目 KB 概览 — 返回 bible、角色、伏笔、大纲等数据的计数与实际内容。

    供前端 stage-workbench 展示当前项目知识库状态。
    内容做了截断控制，防止数据量过大。
    """
    proj_dir = _project_path(project_id)
    if not proj_dir.exists():
        raise HTTPException(status_code=404, detail="项目不存在")

    def _trunc(text: str, max_len: int = 80) -> str:
        if not text:
            return ""
        return text[:max_len] + ("..." if len(text) > max_len else "")

    # ---------- 1. Bible (世界观规则) ----------
    bible_items = []
    try:
        bible_db = BibleDB(project_id)
        rules = await bible_db.get_rules()
        for r in rules[:30]:
            bible_items.append({
                "category": r.category,
                "content": _trunc(r.content),
                "importance": r.importance,
            })
    except Exception:
        pass

    # ---------- 2. Foreshadowing (伏笔) ----------
    fs_items = []
    try:
        fs_db = ForeshadowingDB(project_id)
        fs_list = await fs_db.get_all_items()
        for item in fs_list[:20]:
            fs_items.append({
                "type": item.type,
                "description": _trunc(item.description),
                "status": item.status,
            })
    except Exception:
        pass

    # ---------- 3. Characters (角色卡) ----------
    char_items = []
    try:
        char_db = CharacterDB(project_id)
        raw_chars = await char_db.list_all_characters()
        for data in raw_chars[:30]:
            char_items.append({
                "name": data.get("name", "未命名"),
                "personality_core": _trunc(data.get("personality_core", "")),
                "appearance": _trunc(data.get("appearance", "")),
                "is_protagonist": data.get("is_protagonist", False),
            })
    except Exception:
        pass

    # ---------- 4. Relationships (关系网) ----------
    rel_items = []
    try:
        char_db = CharacterDB(project_id)
        rel_data = await char_db.load("relationships") or {}
        if isinstance(rel_data, dict):
            # 支持两种常见结构: relationships 列表 或 items 列表
            rel_list = rel_data.get("relationships", rel_data.get("items", []))
            if isinstance(rel_list, list):
                for r in rel_list[:30]:
                    rel_items.append({
                        "source": r.get("source_name", r.get("source", "")),
                        "target": r.get("target_name", r.get("target", "")),
                        "type": r.get("relation_type", r.get("type", "")),
                    })
    except Exception:
        pass

    # ---------- 5. Outline (大纲) ----------
    outline_items = []
    try:
        story_db = StoryDB(project_id)
        outline = await story_db.get_outline() or {}
        chaps = outline.get("chapters", []) if isinstance(outline, dict) else []
        if isinstance(chaps, list):
            for c in chaps[:30]:
                outline_items.append({
                    "number": c.get("chapter_number", c.get("number", 0)),
                    "title": c.get("title", ""),
                    "goal": _trunc(c.get("chapter_goal", c.get("goal", ""))),
                })
    except Exception:
        pass

    # ---------- 6. Arcs (故事弧) ----------
    arc_items = []
    try:
        story_db = StoryDB(project_id)
        arc_list = await story_db.list_arc_plans()
        for a in arc_list[:10]:
            arc_items.append({
                "id": a.get("arc_id", a.get("id", "")),
                "title": _trunc(a.get("arc_theme", a.get("title", ""))),
            })
    except Exception:
        pass

    # ---------- 7. Chapter Plans (章节规划) ----------
    plan_items = []
    try:
        story_db = StoryDB(project_id)
        # StoryDB 内部 key 为 chapter_{n}_plan，遍历 story/ 目录
        plan_keys = [k for k in await story_db.list_keys() if k.startswith("chapter_") and k.endswith("_plan")]
        for key in sorted(plan_keys)[:10]:
            data = await story_db.load(key) or {}
            # 尝试从 key 提取章节号
            try:
                ch_num = int(key.replace("chapter_", "").replace("_plan", ""))
            except ValueError:
                ch_num = 0
            plan_items.append({
                "chapter_number": data.get("chapter_number", ch_num),
                "title": _trunc(data.get("title", "")),
                "scene_count": len(data.get("scenes", [])),
            })
    except Exception:
        pass

    return {
        "project_id": project_id,
        "bible": {
            "count": len(bible_items),
            "items": bible_items,
        },
        "foreshadowing": {
            "count": len(fs_items),
            "items": fs_items,
        },
        "characters": {
            "count": len(char_items),
            "items": char_items,
        },
        "relationships": {
            "count": len(rel_items),
            "items": rel_items,
        },
        "outline": {
            "chapter_count": len(outline_items),
            "items": outline_items,
        },
        "arcs": {
            "count": len(arc_items),
            "items": arc_items,
        },
        "chapter_plans": {
            "count": len(plan_items),
            "items": plan_items,
        },
    }


# --------------------------------------------------------
# Orchestrator 运行与 SSE
# --------------------------------------------------------
@router.post("/projects/{project_id}/run")
async def run_project(project_id: str, request: RunRequest):
    """
    启动 Director 运行项目。

    调用后前端应立即连接 /stream 获取 SSE 流。
    """
    _project_path(project_id)  # 校验存在

    mgr = get_session_manager()
    sess = await mgr.get_or_create(project_id)

    if sess.is_pump_running:
        raise HTTPException(status_code=409, detail="项目已在运行中")

    await sess.start(request.user_prompt)
    return {"success": True, "status": "started", "session_id": sess.session_id}


@router.get("/projects/{project_id}/stream")
async def stream_project(project_id: str):
    """
    SSE 流式消费 Orchestrator 产出的 StreamPacket。

    事件类型:
        reasoning  — Director/主管的思考摘要
        output     — Writer 等产出的正文 token(或主管的文本输出)
        completed  — 单轮 ReAct 结束(含 res_id / tool_calls / tokens)
        confirm    — 阶段切换确认请求(前端应弹窗等待用户)
        error      — 异常信息

    连接会在以下情况断开:
        1. Director 自然完成(无更多 tool_calls)
        2. confirm 包到达(前端需重新连接 /respond 后续接)
        3. 达到 max_turns 或发生异常
    """
    _project_path(project_id)

    mgr = get_session_manager()
    sess = await mgr.get(project_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在,请先调用 /run")

    async def event_generator():
        async for pkt in sess.consume():
            yield _stream_packet_to_sse(pkt)
        # 流正常结束
        yield {
            "event": "done",
            "data": json.dumps({"message": "流结束"}, ensure_ascii=False),
        }

    return EventSourceResponse(event_generator(), ping=15)


@router.post("/projects/{project_id}/respond")
async def respond_project(project_id: str, request: RespondRequest):
    """
    用户对 confirm 请求的回复(确认或修改意见)。

    调用后前端应重新连接 /stream 获取续接后的 SSE 流。
    """
    _project_path(project_id)

    mgr = get_session_manager()
    sess = await mgr.get(project_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    if not sess.is_waiting_confirm:
        raise HTTPException(status_code=409, detail="当前不在等待确认状态")

    await sess.approve(request.reply)
    return {"success": True, "status": "continued", "session_id": sess.session_id}


@router.post("/projects/{project_id}/command")
async def send_command(project_id: str, request: CommandRequest):
    """
    向活跃会话的 Director 发送任意用户指令。

    使用场景:
        1. 系统卡住时,用户主动发指令催促/纠正 Director。
        2. 用户想中途补充需求(如"加一个新角色")。
        3. 断点续接:点击"继续 X 阶段"时,把阶段目标作为指令发给 Director。

    行为:
        - pump_running=true: 取消当前 pump,清除 confirm 状态,用 instruction 启动新 pump。
        - waiting_confirm=true: 清除 confirm,用 instruction 当作 approve 续接。
        - 其他: 直接用 instruction 启动新 pump。
    """
    _project_path(project_id)

    mgr = get_session_manager()
    sess = await mgr.get_or_create(project_id)

    # 取消当前 pump(如果有)，等待它真正结束，避免并发 response_id 冲突
    if sess.is_pump_running:
        await sess.cancel_pump()
        # 给前端一个简短窗口处理队列中剩余的 packet
        await asyncio.sleep(0.3)

    # 清除 confirm 状态
    if sess.orch.confirm_payload:
        sess.orch._confirm_payload = None

    # 启动新 pump
    await sess.start(request.instruction)
    return {"success": True, "status": "started", "session_id": sess.session_id}


# --------------------------------------------------------
# 辅助:StreamPacket → SSE 事件
# --------------------------------------------------------
def _stream_packet_to_sse(pkt: StreamPacket) -> dict:
    """把 StreamPacket 转成 SSE 事件字典。"""
    base = {"agent": pkt.agent_name}
    if pkt.type == "reasoning":
        return {
            "event": "reasoning",
            "data": json.dumps({**base, "content": pkt.content}, ensure_ascii=False),
        }
    elif pkt.type == "output":
        return {
            "event": "output",
            "data": json.dumps({**base, "content": pkt.content}, ensure_ascii=False),
        }
    elif pkt.type == "completed":
        if isinstance(pkt.content, CompletedPayload):
            return {
                "event": "completed",
                "data": json.dumps({
                    **base,
                    "res_id": pkt.content.res_id,
                    "total_tokens": pkt.content.total_tokens,
                    "output_types": pkt.content.output_types,
                    "tool_calls": [
                        {"call_id": tc.call_id, "name": tc.name, "arguments": tc.arguments}
                        for tc in pkt.content.tool_calls
                    ],
                }, ensure_ascii=False),
            }
        return {"event": "completed", "data": json.dumps({**base, "content": str(pkt.content)}, ensure_ascii=False)}
    elif pkt.type == "confirm":
        if isinstance(pkt.content, ConfirmPayload):
            return {
                "event": "confirm",
                "data": json.dumps({
                    **base,
                    "from_stage": pkt.content.from_stage,
                    "to_stage": pkt.content.to_stage,
                    "summary": pkt.content.summary,
                    "prompt": pkt.content.prompt,
                }, ensure_ascii=False),
            }
        return {"event": "confirm", "data": json.dumps({**base, "content": str(pkt.content)}, ensure_ascii=False)}
    elif pkt.type == "error":
        return {
            "event": "error",
            "data": json.dumps({**base, "error": str(pkt.content)}, ensure_ascii=False),
        }
    else:
        return {
            "event": pkt.type,
            "data": json.dumps({**base, "content": str(pkt.content)}, ensure_ascii=False),
        }
