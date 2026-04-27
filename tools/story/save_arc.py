"""
tools/story/save_arc.py

保存单个故事弧规划。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.story.save_arc")


class SaveArc(BaseTool):
    @property
    def name(self) -> str:
        return "save_arc"

    @property
    def description(self) -> str:
        return (
            "保存故事弧规划。必填 arc_id;arc_data 含 arc_number / arc_theme / "
            "chapter_range[start,end] / arc_goal / is_placeholder 等字段。"
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
                    "arc_id": {"type": "string"},
                    "arc_data": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
                "required": ["arc_id", "arc_data"],
                "additionalProperties": False,
            },
        }

    async def execute(self, arc_id: str, arc_data: dict, **kwargs) -> str:
        if not arc_id:
            return json.dumps({"error": "arc_id 不能为空"}, ensure_ascii=False)
        if not isinstance(arc_data, dict):
            return json.dumps({"error": "arc_data 必须是对象"}, ensure_ascii=False)

        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            ok = await StoryDB(pid).save_arc_plan(arc_id, arc_data)
            return json.dumps({"saved": ok, "arc_id": arc_id}, ensure_ascii=False)
        except Exception as e:
            logger.exception("save_arc 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
