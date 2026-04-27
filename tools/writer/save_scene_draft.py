"""
tools/writer/save_scene_draft.py

保存某个 scene 的 Writer 输出到章节草稿。
落到 `workspace/{pid}/story/chapter_{n}_draft.json` 的 scenes 数组中。

实现复用 StoryDB.update_scene_in_draft —— 它已经做了"按 scene_index 替换或追加"
的并发安全逻辑,工具只需把 LLM 给的 dict 透传即可。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.checkpoint_db import SceneShowrunnerCheckpointDB
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.writer.save_scene_draft")


class SaveSceneDraft(BaseTool):
    @property
    def name(self) -> str:
        return "save_scene_draft"

    @property
    def description(self) -> str:
        return (
            "保存某个场景的草稿文本到对应章节的 chapter_n_draft.json。scene 必含 "
            "scene_index 与 text;同 scene_index 已存在时替换。"
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
                    "chapter_number": {"type": "integer"},
                    "scene": {
                        "type": "object",
                        "properties": {
                            "scene_index": {"type": "integer"},
                            "text": {"type": "string"},
                        },
                        "required": ["scene_index", "text"],
                        "additionalProperties": True,
                    },
                },
                "required": ["chapter_number", "scene"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self, chapter_number: int, scene: dict, **kwargs
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        if "scene_index" not in scene:
            return json.dumps(
                {"error": "scene 必须含 scene_index"}, ensure_ascii=False
            )

        try:
            ok = await StoryDB(pid).update_scene_in_draft(
                chapter_number, scene
            )
            if ok:
                await SceneShowrunnerCheckpointDB(pid).save_checkpoint(
                    chapter_number=chapter_number,
                    scene_index=scene["scene_index"],
                    step="draft",
                    extra={"rewrite_attempt": scene.get("rewrite_attempt", 0)},
                )
            return json.dumps(
                {
                    "saved": ok,
                    "chapter_number": chapter_number,
                    "scene_index": scene["scene_index"],
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("save_scene_draft 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
