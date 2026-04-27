"""
tools/kb_query/kb_context_assembler.py

KBContextAssembler — 纯函数工具，零 LLM token 消耗。

职责:
    在 SceneShowrunner 调 SceneDirector 之前，对 KB 数据做预过滤和精简，
    只保留与当前场景直接相关的信息，避免无关设定淹没 SceneDirector 的上下文窗口。

节省效果:
    - 优化前:全量 character + 全量 bible + 全量 foreshadowing ≈ 3000-5000 字
    - 优化后:精简 briefs + 相关 rules + 场景级伏笔 ≈ 800-1500 字

输入:
    chapter_number / scene_index / present_characters / location_hint(可选) /
    foreshadowing_ids(可选)
输出:
    {
      "character_briefs": [{name, current_emotion, active_goals, voice_keywords}],
      "world_context": {"location": {...}, "relevant_rules": [...]},
      "active_foreshadowing": [{id, description, status, urgency}],
      "token_estimate": 1200
    }
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.character_db import CharacterDB
from knowledge_bases.bible_db import BibleDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB
from knowledge_bases.geo_db import GeoDB

logger = get_logger("tools.kb_query.kb_context_assembler")


def _estimate_tokens(text: str) -> int:
    """粗略估算中文字符对应的 token 数(中文字符 ≈ 1.5 token)。"""
    return int(len(text) * 1.5)


class KBContextAssembler(BaseTool):
    """
    KB 上下文预过滤组装器。

    设计原则:
        - 纯函数，不调用 LLM，直接读取 KB 文件并过滤字段
        - 出场角色只取"当前必要状态"，不取完整背景故事
        - Bible 规则只保留 content，去掉 metadata 噪音
        - Foreshadowing 按场景精确过滤，resolved 状态的已自动排除
    """

    @property
    def name(self) -> str:
        return "kb_context_assembler"

    @property
    def description(self) -> str:
        return (
            "组装当前场景的精简 KB 上下文包。"
            "输入出场角色列表和可选地点提示，输出过滤后的 character_briefs + world_context + active_foreshadowing。"
            "零 token 消耗，纯函数工具。"
        )

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {
                        "type": "integer",
                        "description": "当前章节号",
                    },
                    "scene_index": {
                        "type": "integer",
                        "description": "当前场景索引",
                    },
                    "present_characters": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "出场角色名列表(必填)。只返回这些角色的精简状态。",
                    },
                    "location_hint": {
                        "type": "string",
                        "description": "场景发生地名称或节点 ID(可选)。提供后查询 geo_node 获取感官细节。",
                    },
                    "foreshadowing_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "场景计划显式指定要触发或埋入的伏笔 ID 列表(可选)。",
                    },
                },
                "required": ["chapter_number", "scene_index", "present_characters"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        chapter_number: int,
        scene_index: int,
        present_characters: list[str],
        location_hint: str = "",
        foreshadowing_ids: list[str] | None = None,
        **kwargs,
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            # ── 1. 角色精简 ──
            character_briefs = []
            char_db = CharacterDB(pid)
            for char_name in present_characters:
                char = await char_db.get_character(char_name)
                if char is None:
                    continue
                brief = {
                    "name": char.name,
                    "current_emotion": char.current_emotion or "",
                    "active_goals": char.active_goals or [],
                    "voice_keywords": char.voice_keywords or [],
                }
                # 若角色有 cultivation，追加修为信息（战斗/突破场景需要）
                if char.cultivation:
                    brief["cultivation_realm"] = char.cultivation.realm or ""
                    brief["cultivation_stage"] = char.cultivation.stage or ""
                character_briefs.append(brief)

            # ── 2. 世界观精简 ──
            world_context = {}

            # 地点信息(若提供)
            if location_hint:
                geo_db = GeoDB(pid)
                node = None
                # 先按 ID 试，再按名称试
                node = await geo_db.get_node(location_hint)
                if not node:
                    node = await geo_db.get_node_by_name(location_hint)
                if node:
                    world_context["location"] = {
                        "name": node.name,
                        "description": node.description or "",
                        "node_type": node.node_type or "",
                        "scale": node.scale or "",
                    }

            # Bible 规则精简(只保留 content + category，去掉 source_chapter/importance 等)
            bible_db = BibleDB(pid)
            rules = await bible_db.get_rules()
            relevant_rules = []
            for rule in rules:
                relevant_rules.append({
                    "id": rule.id,
                    "content": rule.content,
                    "category": str(rule.category.value) if rule.category else "",
                })
            world_context["relevant_rules"] = relevant_rules

            # ── 3. 伏笔按场景精确过滤 ──
            fs_db = ForeshadowingDB(pid)
            active_items = await fs_db.get_active_for_scene(
                chapter_number=chapter_number,
                scene_index=scene_index,
                trigger_ids=foreshadowing_ids or [],
            )
            active_foreshadowing = []
            for item in active_items:
                active_foreshadowing.append({
                    "id": item.id,
                    "description": item.description,
                    "status": str(item.status.value) if item.status else "",
                    "urgency": item.urgency,
                    "type": str(item.type.value) if item.type else "",
                })

            # ── 4. Token 估算 ──
            ctx_text = json.dumps(
                {
                    "character_briefs": character_briefs,
                    "world_context": world_context,
                    "active_foreshadowing": active_foreshadowing,
                },
                ensure_ascii=False,
            )
            token_estimate = _estimate_tokens(ctx_text)

            result = {
                "chapter_number": chapter_number,
                "scene_index": scene_index,
                "character_briefs": character_briefs,
                "world_context": world_context,
                "active_foreshadowing": active_foreshadowing,
                "token_estimate": token_estimate,
            }
            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            logger.exception("kb_context_assembler 失败")
            return json.dumps({"error": f"组装失败: {e}"}, ensure_ascii=False)
