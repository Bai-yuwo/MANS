"""
tools/story/save_outline.py

保存全局大纲到 StoryDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.story.save_outline")


class SaveOutline(BaseTool):
    @property
    def name(self) -> str:
        return "save_outline"

    @property
    def description(self) -> str:
        return (
            "保存项目全局大纲。outline 字段任意,由 PlotArchitect 自定义结构,"
            "推荐含 main_thread / arcs_overview / themes / ending_direction。"
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
                    "outline": {
                        "type": "object",
                        "additionalProperties": True,
                    }
                },
                "required": ["outline"],
                "additionalProperties": False,
            },
        }

    async def execute(self, outline: dict, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        if not isinstance(outline, dict):
            return json.dumps({"error": "outline 必须是对象"}, ensure_ascii=False)

        try:
            ok = await StoryDB(pid).save_outline(outline)
            return json.dumps({"saved": ok}, ensure_ascii=False)
        except Exception as e:
            logger.exception("save_outline 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
