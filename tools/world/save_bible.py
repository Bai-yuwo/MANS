"""
tools/world/save_bible.py

WorldArchitect 主管的写工具 — 批量追加 / 写入世界规则到 BibleDB。

设计:Bible 遵循"只增不减"原则,因此本工具语义是 **append**:
    主管每次调用传入一个 rules 数组,工具逐条转 Pydantic 验证后追加。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import WorldRule
from knowledge_bases.bible_db import BibleDB

logger = get_logger("tools.world.save_bible")


class SaveBible(BaseTool):
    @property
    def name(self) -> str:
        return "save_bible"

    @property
    def description(self) -> str:
        return (
            "向 Bible 追加世界规则(append-only)。rules 为对象数组,每条须含 "
            "category(cultivation/geography/social/physics/special)、content、"
            "source_chapter、importance(critical/major/minor)。"
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
                    "rules": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "category": {"type": "string"},
                                "content": {"type": "string"},
                                "source_chapter": {"type": "integer"},
                                "importance": {"type": "string"},
                            },
                            "required": ["category", "content"],
                            "additionalProperties": True,
                        },
                    }
                },
                "required": ["rules"],
                "additionalProperties": False,
            },
        }

    async def execute(self, rules: list[dict], **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        if not rules:
            return json.dumps({"error": "rules 不能为空"}, ensure_ascii=False)

        try:
            db = BibleDB(pid)
            saved = 0
            failed: list[dict] = []
            for raw in rules:
                try:
                    rule = WorldRule(**raw)
                except Exception as e:
                    failed.append({"rule": raw, "error": str(e)})
                    continue
                ok = await db.append_rule(rule)
                if ok:
                    saved += 1
                else:
                    failed.append({"rule": raw, "error": "append_rule 返回 False"})
            return json.dumps(
                {"saved": saved, "failed": failed, "total": len(rules)},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("save_bible 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
