"""
tools/review/save_review_issues.py

落 Critic + ContinuityChecker 合并后的 issues 到
`workspace/{pid}/review/chapter_{n}_scene_{i}_issues.json`。

非正式 KB,主要用于审计回看。
"""

import json

from knowledge_bases.base_db import BaseDB

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import ReviewIssues

logger = get_logger("tools.review.save_review_issues")


class SaveReviewIssues(BaseTool):
    @property
    def name(self) -> str:
        return "save_review_issues"

    @property
    def description(self) -> str:
        return (
            "保存某场景的审查 issues(Critic + ContinuityChecker 合并)。"
            "data 字段对齐 schemas.ReviewIssues,必含 critic_issues[] 与 continuity_issues[]。"
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
                    "data": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
                "required": ["chapter_number", "scene_index", "data"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        chapter_number: int,
        scene_index: int,
        data: dict,
        **kwargs,
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            obj = ReviewIssues(**data)
        except Exception as e:
            return json.dumps(
                {"error": f"ReviewIssues 校验失败: {e}"},
                ensure_ascii=False,
            )

        try:
            db = BaseDB(pid, "review")
            key = f"chapter_{chapter_number}_scene_{scene_index}_issues"
            ok = await db.save(key, obj.model_dump())
            return json.dumps(
                {
                    "saved": ok,
                    "chapter_number": chapter_number,
                    "scene_index": scene_index,
                    "max_severity": (obj.max_severity.value if obj.max_severity else None),
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("save_review_issues 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
