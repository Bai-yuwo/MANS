"""
tools/kb_query/read_chapter_plan.py

读取指定章节的规划(场景序列)。

数据源:`workspace/{project_id}/story/chapter_{n}_plan.json`(StoryDB)。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.kb_query.read_chapter_plan")


class ReadChapterPlan(BaseTool):
    @property
    def name(self) -> str:
        return "read_chapter_plan"

    @property
    def description(self) -> str:
        return "读取指定章节的规划(场景序列、场景目标、情绪走向等)。"

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
                        "description": "章节编号(从 1 开始)。",
                    }
                },
                "required": ["chapter_number"],
                "additionalProperties": False,
            },
        }

    async def execute(self, chapter_number: int, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = StoryDB(pid)
            plan = await db.get_chapter_plan(chapter_number)
            if plan is None:
                return json.dumps(
                    {
                        "error": f"章节 {chapter_number} 的规划尚未生成",
                        "chapter_number": chapter_number,
                    },
                    ensure_ascii=False,
                )
            return json.dumps(plan.model_dump(), ensure_ascii=False)
        except Exception as e:
            logger.exception("读取 chapter_plan 失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
