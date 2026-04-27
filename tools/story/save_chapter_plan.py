"""
tools/story/save_chapter_plan.py

保存章节规划(场景序列)。

入参形态:plan 完整 ChapterPlan dict。会经 Pydantic 验证。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import ChapterPlan
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.story.save_chapter_plan")


class SaveChapterPlan(BaseTool):
    @property
    def name(self) -> str:
        return "save_chapter_plan"

    @property
    def description(self) -> str:
        return (
            "保存章节规划。plan 必须含 chapter_number 与 scenes 数组(每个 scene "
            "含 scene_index / intent / pov_character / target_word_count 等字段),"
            "字段对齐 schemas.ChapterPlan。"
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
                    "plan": {
                        "type": "object",
                        "properties": {
                            "chapter_number": {"type": "integer"},
                            "scenes": {"type": "array"},
                        },
                        "required": ["chapter_number"],
                        "additionalProperties": True,
                    }
                },
                "required": ["plan"],
                "additionalProperties": False,
            },
        }

    async def execute(self, plan: dict, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            chapter_plan = ChapterPlan(**plan)
        except Exception as e:
            return json.dumps(
                {"error": f"ChapterPlan 校验失败: {e}"},
                ensure_ascii=False,
            )

        try:
            ok = await StoryDB(pid).save_chapter_plan(chapter_plan)
            return json.dumps(
                {"saved": ok, "chapter_number": chapter_plan.chapter_number},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("save_chapter_plan 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
