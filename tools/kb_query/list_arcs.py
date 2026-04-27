"""
tools/kb_query/list_arcs.py

列出项目中所有故事弧规划。

数据源: workspace/{project_id}/story/arcs/arc_*.json 通过 StoryDB。

返回:
    {
        "count": 3,
        "arcs": [
            {"arc_id": "arc_1", "title": "...", "chapter_range": [1, 5]},
            ...
        ]
    }
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.kb_query.list_arcs")


class ListArcs(BaseTool):
    @property
    def name(self) -> str:
        return "list_arcs"

    @property
    def description(self) -> str:
        return (
            "列出项目中所有故事弧规划的摘要。"
            "返回每个弧线的 arc_id / title / chapter_range，"
            "用于断点续接或编排时快速了解已有弧线结构。"
        )

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
            arc_list = await db.list_arc_plans()
            arcs = []
            for a in arc_list:
                arcs.append({
                    "arc_id": a.get("arc_id", a.get("id", "")),
                    "title": a.get("arc_theme", a.get("title", "")),
                    "chapter_range": a.get("chapter_range", []),
                    "is_placeholder": a.get("is_placeholder", False),
                })
            return json.dumps(
                {"count": len(arcs), "arcs": arcs},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("列出弧线失败")
            return json.dumps({"error": f"列出失败: {e}"}, ensure_ascii=False)
