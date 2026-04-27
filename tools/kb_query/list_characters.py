"""
tools/kb_query/list_characters.py

列出项目中所有已保存的角色。

数据源: workspace/{project_id}/characters/*.json 通过 CharacterDB。

返回:
    JSON 字符串,形如 {"characters": [{"name": "...", "role": "...", "is_protagonist": true}, ...]}
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.character_db import CharacterDB

logger = get_logger("tools.kb_query.list_characters")


class ListCharacters(BaseTool):
    @property
    def name(self) -> str:
        return "list_characters"

    @property
    def description(self) -> str:
        return (
            "列出项目中所有已保存的角色卡摘要。"
            "返回每个角色的 name / role / is_protagonist 等关键字段，"
            "用于断点续接时检查已有角色阵容。"
        )

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(self, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = CharacterDB(pid)
            raw_chars = await db.list_all_characters()
            characters = []
            for data in raw_chars:
                characters.append({
                    "name": data.get("name", "未命名"),
                    "role": data.get("role", ""),
                    "is_protagonist": data.get("is_protagonist", False),
                    "gender": data.get("gender", ""),
                    "personality_core": data.get("personality_core", "")[:60],
                })
            return json.dumps(
                {"count": len(characters), "characters": characters},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("列出角色失败")
            return json.dumps({"error": f"列出角色失败: {e}"}, ensure_ascii=False)
