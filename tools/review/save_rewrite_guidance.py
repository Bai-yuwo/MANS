"""
tools/review/save_rewrite_guidance.py

落 ReviewManager 仲裁产出的 RewriteGuidance,供 Writer 重写时读取。

存储:`workspace/{pid}/review/chapter_{n}_scene_{i}_guidance_attempt_{k}.json`,
按 rewrite_attempt 区分多次重写的指南。
"""

import json

from knowledge_bases.base_db import BaseDB

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import RewriteGuidance
from knowledge_bases.checkpoint_db import SceneShowrunnerCheckpointDB

logger = get_logger("tools.review.save_rewrite_guidance")


class SaveRewriteGuidance(BaseTool):
    @property
    def name(self) -> str:
        return "save_rewrite_guidance"

    @property
    def description(self) -> str:
        return (
            "保存 ReviewManager 产出的 RewriteGuidance(必含 needs_rewrite / "
            "priority_issues / must_keep / must_change / style_hints / rewrite_attempt)。"
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
                    "scene_index": {"type": "integer"},
                    "guidance": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
                "required": ["chapter_number", "scene_index", "guidance"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        chapter_number: int,
        scene_index: int,
        guidance: dict,
        **kwargs,
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            obj = RewriteGuidance(**guidance)
        except Exception as e:
            return json.dumps(
                {"error": f"RewriteGuidance 校验失败: {e}"},
                ensure_ascii=False,
            )

        try:
            db = BaseDB(pid, "review")
            key = (
                f"chapter_{chapter_number}_scene_{scene_index}"
                f"_guidance_attempt_{obj.rewrite_attempt}"
            )
            ok = await db.save(key, obj.model_dump())
            if ok:
                await SceneShowrunnerCheckpointDB(pid).save_checkpoint(
                    chapter_number=chapter_number,
                    scene_index=scene_index,
                    step="rewrite_guidance",
                    extra={"rewrite_attempt": obj.rewrite_attempt},
                )
            return json.dumps(
                {
                    "saved": ok,
                    "chapter_number": chapter_number,
                    "scene_index": scene_index,
                    "rewrite_attempt": obj.rewrite_attempt,
                    "needs_rewrite": obj.needs_rewrite,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("save_rewrite_guidance 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
