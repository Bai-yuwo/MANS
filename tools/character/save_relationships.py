"""
tools/character/save_relationships.py

保存/更新角色关系网。

存储位置:`workspace/{pid}/characters/relationships.json` —— 与角色卡共享 characters/ 目录。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.character_db import CharacterDB

logger = get_logger("tools.character.save_relationships")


class SaveRelationships(BaseTool):
    @property
    def name(self) -> str:
        return "save_relationships"

    @property
    def description(self) -> str:
        return (
            "保存全局角色关系网。data 是任意结构的 JSON,典型字段如 "
            "graph(邻接表)/nodes/edges 等。已存在时按深度合并更新。"
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
                    "data": {
                        "type": "object",
                        "description": "关系网 JSON,结构由 RelationDesigner 决定。",
                        "additionalProperties": True,
                    }
                },
                "required": ["data"],
                "additionalProperties": False,
            },
        }

    async def execute(self, data: dict, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        if not isinstance(data, dict):
            return json.dumps(
                {"error": "data 必须是对象"}, ensure_ascii=False
            )

        try:
            ok = await CharacterDB(pid).save("relationships", data)
            return json.dumps({"saved": ok}, ensure_ascii=False)
        except Exception as e:
            logger.exception("save_relationships 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
