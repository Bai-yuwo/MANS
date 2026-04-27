"""
tools/character/save_character.py

保存/更新单个角色卡到 CharacterDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import CharacterCard
from knowledge_bases.character_db import CharacterDB

logger = get_logger("tools.character.save_character")


class SaveCharacter(BaseTool):
    @property
    def name(self) -> str:
        return "save_character"

    @property
    def description(self) -> str:
        return (
            "保存/更新单个角色卡(CharacterCard)。完整字段见 schemas.CharacterCard,"
            "至少需要 name、gender、role、appearance、personality 等基础字段。"
            "已存在时按深度合并更新。"
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
                    "character": {
                        "type": "object",
                        "description": "完整角色卡 JSON,字段对齐 schemas.CharacterCard。",
                        "properties": {
                            "name": {"type": "string"},
                        },
                        "required": ["name"],
                        "additionalProperties": True,
                    }
                },
                "required": ["character"],
                "additionalProperties": False,
            },
        }

    async def execute(self, character: dict, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            card = CharacterCard(**character)
        except Exception as e:
            return json.dumps(
                {"error": f"CharacterCard 校验失败: {e}", "character": character},
                ensure_ascii=False,
            )

        try:
            ok = await CharacterDB(pid).save_character(card)
            return json.dumps(
                {"saved": ok, "name": card.name},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("save_character 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
