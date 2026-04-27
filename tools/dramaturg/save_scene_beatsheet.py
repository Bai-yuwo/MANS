"""
tools/dramaturg/save_scene_beatsheet.py

把 SceneDirector 产出的 SceneBeatsheet 写入
`workspace/{pid}/chapters/scene_beatsheets/scene_{scene_index}.json`。

SceneShowrunner 主管在调用 SceneDirector 拿到 dict 后,经过审阅再调本工具落盘。
"""

import json

from knowledge_bases.base_db import BaseDB

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import SceneBeatsheet

logger = get_logger("tools.dramaturg.save_scene_beatsheet")


class SaveSceneBeatsheet(BaseTool):
    @property
    def name(self) -> str:
        return "save_scene_beatsheet"

    @property
    def description(self) -> str:
        return (
            "保存场景节拍表。beatsheet 必含 chapter_number / scene_index / "
            "sensory_requirements / action_beats[] / emotional_beats[],"
            "字段对齐 schemas.SceneBeatsheet。"
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
                    "beatsheet": {
                        "type": "object",
                        "properties": {
                            "chapter_number": {"type": "integer"},
                            "scene_index": {"type": "integer"},
                        },
                        "required": ["chapter_number", "scene_index"],
                        "additionalProperties": True,
                    }
                },
                "required": ["beatsheet"],
                "additionalProperties": False,
            },
        }

    async def execute(self, beatsheet: dict, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            obj = SceneBeatsheet(**beatsheet)
        except Exception as e:
            return json.dumps(
                {"error": f"SceneBeatsheet 校验失败: {e}"},
                ensure_ascii=False,
            )

        try:
            db = BaseDB(pid, "chapters/scene_beatsheets")
            ok = await db.save(f"scene_{obj.scene_index}", obj.model_dump())
            return json.dumps(
                {"saved": ok, "scene_index": obj.scene_index},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("save_scene_beatsheet 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
