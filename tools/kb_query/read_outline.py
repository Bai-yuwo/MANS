"""
tools/kb_query/read_outline.py

读取全局大纲。

数据源:`workspace/{project_id}/story/outline.json` 通过 `StoryDB.get_outline`。
返回原始字典(由 OutlineGenerator/PlotArchitect 决定具体字段)。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.kb_query.read_outline")


class ReadOutline(BaseTool):
    @property
    def name(self) -> str:
        return "read_outline"

    @property
    def description(self) -> str:
        return "读取项目的全局大纲(主线、关键节点、结局方向)。"

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(self, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = StoryDB(pid)
            data = await db.get_outline()
            if data is None:
                return json.dumps(
                    {"error": "大纲尚未生成", "outline": None}, ensure_ascii=False
                )
            return json.dumps({"outline": data}, ensure_ascii=False)
        except Exception as e:
            logger.exception("读取 outline 失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
