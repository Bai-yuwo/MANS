"""
tools/character/delete_character.py

删除指定角色卡。

存储位置: workspace/{pid}/characters/{name}.json

注意：
    这是不可逆操作。由 CastingDirector 在响应用户"清理/删除角色"指令时调用。
    删除前应先 list_characters 确认目标存在。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.character_db import CharacterDB

logger = get_logger("tools.character.delete_character")


class DeleteCharacter(BaseTool):
    @property
    def name(self) -> str:
        return "delete_character"

    @property
    def description(self) -> str:
        return (
            "删除指定角色卡。name 为角色姓名(与文件名一致)。"
            "删除后不可恢复。"
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
                    "name": {
                        "type": "string",
                        "description": "要删除的角色姓名(必须与角色卡 name 字段一致)",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        }

    async def execute(self, name: str, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        if not name:
            return json.dumps({"error": "name 不能为空"}, ensure_ascii=False)

        try:
            db = CharacterDB(pid)
            ok = await db.delete(name)
            if ok:
                logger.info(f"角色已删除: {name}")
                return json.dumps(
                    {"deleted": True, "name": name},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"deleted": False, "name": name, "error": "删除失败"},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception(f"删除角色失败: {name}")
            return json.dumps(
                {"error": f"删除失败: {e}"},
                ensure_ascii=False,
            )
