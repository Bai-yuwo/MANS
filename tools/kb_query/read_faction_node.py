"""
tools/kb_query/read_faction_node.py

读取单个势力节点详情（含关系列表）。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.faction_db import FactionDB

logger = get_logger("tools.kb_query.read_faction_node")


class ReadFactionNode(BaseTool):
    @property
    def name(self) -> str:
        return "read_faction_node"

    @property
    def description(self) -> str:
        return (
            "读取单个势力的完整详情，包括立场、领袖、控制的领土、"
            "与其他势力的关系（敌对/同盟/隶属等）。"
            "支持通过 node_id 或 name 查询。"
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
                    "node_id": {
                        "type": "string",
                        "description": "势力节点 ID（优先使用）",
                    },
                    "name": {
                        "type": "string",
                        "description": "势力名称（当 node_id 未知时使用）",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(self, node_id: str = "", name: str = "", **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        if not node_id and not name:
            return json.dumps({"error": "node_id 和 name 至少提供一个"}, ensure_ascii=False)

        try:
            db = FactionDB(pid)
            node = None
            if node_id:
                node = await db.get_node(node_id)
            if not node and name:
                node = await db.get_node_by_name(name)

            if not node:
                return json.dumps({"error": "势力不存在"}, ensure_ascii=False)

            return json.dumps(
                {"node": node.model_dump()},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("读取势力节点失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
