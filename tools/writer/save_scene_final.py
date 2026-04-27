"""
tools/writer/save_scene_final.py

把整章终稿写入 `workspace/{pid}/chapters/chapter_{n}_final.json`。

LLM 一般不直接构造 ChapterFinal,而是 SceneShowrunner 在审阅完最后一个场景的
draft 后,组装 ChapterFinal(汇总 scene_texts、统计字数、生成 summary)然后调本工具。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import ChapterFinal
from knowledge_bases.checkpoint_db import SceneShowrunnerCheckpointDB
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.writer.save_scene_final")


class SaveSceneFinal(BaseTool):
    @property
    def name(self) -> str:
        return "save_scene_final"

    @property
    def description(self) -> str:
        return (
            "保存整章终稿(ChapterFinal)。final 必含 chapter_number / title / "
            "full_text / word_count / scene_texts[] / summary,字段对齐 schemas.ChapterFinal。"
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
                    "final": {
                        "type": "object",
                        "properties": {
                            "chapter_number": {"type": "integer"},
                            "title": {"type": "string"},
                        },
                        "required": ["chapter_number", "title"],
                        "additionalProperties": True,
                    }
                },
                "required": ["final"],
                "additionalProperties": False,
            },
        }

    async def execute(self, final: dict, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            obj = ChapterFinal(**final)
        except Exception as e:
            return json.dumps(
                {"error": f"ChapterFinal 校验失败: {e}"},
                ensure_ascii=False,
            )

        try:
            ok = await StoryDB(pid).save_chapter_final(obj)
            if ok:
                for idx, _ in enumerate(obj.scene_texts or []):
                    await SceneShowrunnerCheckpointDB(pid).save_checkpoint(
                        chapter_number=obj.chapter_number,
                        scene_index=idx,
                        step="final",
                    )
            return json.dumps(
                {
                    "saved": ok,
                    "chapter_number": obj.chapter_number,
                    "word_count": obj.word_count,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("save_scene_final 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
