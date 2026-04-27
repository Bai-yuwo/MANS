"""
tools/kb_query/read_bible.py

读取世界观规则(Bible)。

数据源:`workspace/{project_id}/bible/world_rules.json` 通过 `BibleDB`。

支持参数:
    - category: 可选,枚举 cultivation / geography / social / physics / special。
                None 时返回全部。

返回:
    JSON 字符串,形如 {"rules": [{"id": ..., "category": ..., "content": ...}, ...]}
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.bible_db import BibleDB

logger = get_logger("tools.kb_query.read_bible")


class ReadBible(BaseTool):
    @property
    def name(self) -> str:
        return "read_bible"

    @property
    def description(self) -> str:
        return "读取项目世界观规则(Bible),可选按 category 筛选(cultivation/geography/social/physics/special)。"

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "cultivation",
                            "geography",
                            "social",
                            "physics",
                            "special",
                        ],
                        "description": "可选分类筛选,留空则返回全部规则。",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(self, category: str | None = None, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = BibleDB(pid)
            rules = await db.get_rules(category=category)
            return json.dumps(
                {
                    "category": category,
                    "count": len(rules),
                    "rules": [r.model_dump() for r in rules],
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("读取 bible 失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
