"""
tools/kb_query/read_relationships.py

读取角色关系网。

数据源:`workspace/{project_id}/characters/relationships.json`。
若指定 character_name,仅返回该角色的关系列表;否则返回全量关系网。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.character_db import CharacterDB

logger = get_logger("tools.kb_query.read_relationships")


class ReadRelationships(BaseTool):
    @property
    def name(self) -> str:
        return "read_relationships"

    @property
    def description(self) -> str:
        return (
            "读取角色关系网。不传 character_name 返回全量;传则只返回该角色"
            "的关系列表(适合对话场景前查情感倾向)。"
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
                    "character_name": {
                        "type": "string",
                        "description": "可选,只返回该角色的关系列表。",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(self, character_name: str | None = None, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = CharacterDB(pid)

            # 全局关系网作为单 key 存于 characters/ 子目录下
            global_rel = await db.load("relationships") or {}

            if character_name is None:
                return json.dumps(
                    {"global": global_rel}, ensure_ascii=False
                )

            char = await db.get_character(character_name)
            if char is None:
                return json.dumps(
                    {
                        "error": f"角色不存在: {character_name}",
                        "character_name": character_name,
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "character_name": character_name,
                    "relationships": [r.model_dump() for r in char.relationships],
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("读取 relationships 失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
