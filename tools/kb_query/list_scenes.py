"""
tools/kb_query/list_scenes.py

列出指定章节的场景索引(及其节拍表是否就绪)。

输入:chapter_number
输出:
    {
        "chapter_number": 5,
        "scenes": [
            {"scene_index": 1, "has_beatsheet": true, "title": "..."},
            ...
        ]
    }

scene_index 来自 chapter_plan.scenes[i].scene_index。
"""

import json

from knowledge_bases.base_db import BaseDB

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.kb_query.list_scenes")


class ListScenes(BaseTool):
    @property
    def name(self) -> str:
        return "list_scenes"

    @property
    def description(self) -> str:
        return "列出指定章节的场景序号与节拍表就绪状态。"

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
                        "error": f"章节 {chapter_number} 规划不存在",
                        "chapter_number": chapter_number,
                    },
                    ensure_ascii=False,
                )

            beat_db = BaseDB(pid, "chapters/scene_beatsheets")
            existing_keys = set(await beat_db.list_keys())

            scenes_out: list[dict] = []
            for sc in plan.scenes:
                # ScenePlan 在 schemas.py 中字段假设为 scene_index / scene_title
                idx = getattr(sc, "scene_index", None) or getattr(sc, "index", None)
                title = getattr(sc, "scene_title", "") or getattr(sc, "title", "")
                key = f"scene_{idx}" if idx is not None else None
                scenes_out.append(
                    {
                        "scene_index": idx,
                        "title": title,
                        "has_beatsheet": (key in existing_keys) if key else False,
                    }
                )

            return json.dumps(
                {"chapter_number": chapter_number, "scenes": scenes_out},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("列出场景失败")
            return json.dumps({"error": f"列出失败: {e}"}, ensure_ascii=False)
