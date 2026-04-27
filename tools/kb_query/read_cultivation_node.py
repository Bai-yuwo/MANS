"""
tools/kb_query/read_cultivation_node.py

读取单个修为节点详情（含前后节点与分支）。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.cultivation_db import CultivationDB

logger = get_logger("tools.kb_query.read_cultivation_node")


class ReadCultivationNode(BaseTool):
    @property
    def name(self) -> str:
        return "read_cultivation_node"

    @property
    def description(self) -> str:
        return (
            "读取单个修为节点的完整详情，包括能力、限制、前置条件、"
            "上级/下级境界、分支信息。"
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
                        "description": "修为节点 ID（优先使用）",
                    },
                    "name": {
                        "type": "string",
                        "description": "境界名称（当 node_id 未知时使用）",
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
            db = CultivationDB(pid)
            node = None
            if node_id:
                node = await db.get_node(node_id)
            if not node and name:
                node = await db.get_node_by_name(name)

            if not node:
                return json.dumps({"error": "修为节点不存在"}, ensure_ascii=False)

            # 获取分支信息
            branches = await db.get_branches(node.id)

            return json.dumps(
                {
                    "node": node.model_dump(),
                    "branches": [b.model_dump() for b in branches],
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("读取修为节点失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
