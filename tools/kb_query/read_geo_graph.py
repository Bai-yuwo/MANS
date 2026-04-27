"""
tools/kb_query/read_geo_graph.py

展开地理节点层级树（全貌）。

从所有根节点（无 parent_id 的节点）向下展开，返回完整的地理层级结构。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.geo_db import GeoDB

logger = get_logger("tools.kb_query.read_geo_graph")


class ReadGeoGraph(BaseTool):
    @property
    def name(self) -> str:
        return "read_geo_graph"

    @property
    def description(self) -> str:
        return (
            "展开地理节点层级树，返回从根节点（大陆/世界）到叶节点（据点/秘境）的完整地理结构。"
            "用于查看整个世界地图的层级关系，或断点续接时检查已有地理数据。"
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
                    "max_depth": {
                        "type": "integer",
                        "minimum": -1,
                        "description": "展开深度，-1 表示不限深度，默认 -1。",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(self, max_depth: int = -1, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = GeoDB(pid)
            trees = await db.get_full_graph(max_depth=max_depth)
            return json.dumps(
                {"tree_count": len(trees), "trees": trees},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("读取地理图失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
