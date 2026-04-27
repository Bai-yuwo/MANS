"""
tools/kb_query/read_faction_network.py

展开势力关系网。

返回势力节点及其关系边，可选从指定势力为中心展开。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.faction_db import FactionDB

logger = get_logger("tools.kb_query.read_faction_network")


class ReadFactionNetwork(BaseTool):
    @property
    def name(self) -> str:
        return "read_faction_network"

    @property
    def description(self) -> str:
        return (
            "展开势力关系网。返回所有势力节点及其关系边（敌对/同盟/隶属等）。"
            "可选指定中心势力，只展开该中心及其关联势力。"
            "用于查看整个世界观的势力博弈格局，或断点续接时检查已有势力数据。"
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
                    "center_id": {
                        "type": "string",
                        "description": "中心势力 ID，留空则返回全部势力",
                    },
                    "depth": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                        "description": "关系展开深度，默认 2",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(self, center_id: str = "", depth: int = 2, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = FactionDB(pid)
            network = await db.get_network(
                center_id=center_id or None,
                depth=depth,
            )
            return json.dumps(
                {
                    "center_id": network.get("center_id"),
                    "node_count": len(network.get("nodes", {})),
                    "edge_count": len(network.get("edges", [])),
                    "network": network,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("读取势力网失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
