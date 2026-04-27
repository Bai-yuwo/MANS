"""
tools/kb_query/read_character.py

读取单个角色卡。

数据源:`workspace/{project_id}/characters/{name}.json` 通过 `CharacterDB.get_character`。
支持:
    - 精确名匹配
    - 别名匹配(`aliases` 列表)
    - 规范化匹配("刘禅(现代)" → "刘禅")
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.character_db import CharacterDB

logger = get_logger("tools.kb_query.read_character")


class ReadCharacter(BaseTool):
    @property
    def name(self) -> str:
        return "read_character"

    @property
    def description(self) -> str:
        return "按姓名读取角色卡(支持别名与括号注释规范化)。返回完整 CharacterCard JSON。"

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
                        "description": "角色姓名,支持别名,例:'刘禅' / '刘禅(现代)'。",
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
            char = await db.get_character(name)
            if char is None:
                return json.dumps(
                    {"error": f"角色不存在: {name}", "name": name},
                    ensure_ascii=False,
                )
            return json.dumps(char.model_dump(), ensure_ascii=False)
        except Exception as e:
            logger.exception("读取 character 失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
