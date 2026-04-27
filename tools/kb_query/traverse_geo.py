"""
tools/kb_query/traverse_geo.py

沿指定方向递推遍历地理节点。

支持向下（子节点）、向上（父节点）、横向（连接节点）三种方向。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.geo_db import GeoDB

logger = get_logger("tools.kb_query.traverse_geo")


class TraverseGeo(BaseTool):
    @property
    def name(self) -> str:
        return "traverse_geo"

    @property
    def description(self) -> str:
        return (
            "沿指定方向递推遍历地理节点。"
            "例如：从天海市向下遍历 2 步，可得到区→具体地点；"
            "向上遍历可回溯到上级区域（州/大陆）；"
            "横向遍历可获得相邻/相连地点。"
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
                    "start_id": {
                        "type": "string",
                        "description": "起始节点 ID",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up", "lateral"],
                        "description": "遍历方向：down=向下（子节点），up=向上（父节点），lateral=横向（连接节点）",
                    },
                    "steps": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "递推步数，默认 1",
                    },
                },
                "required": ["start_id", "direction"],
                "additionalProperties": False,
            },
        }

    async def execute(self, start_id: str, direction: str, steps: int = 1, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = GeoDB(pid)
            nodes = await db.traverse(start_id, direction, steps)
            return json.dumps(
                {
                    "start_id": start_id,
                    "direction": direction,
                    "steps": steps,
                    "count": len(nodes),
                    "nodes": [n.model_dump() for n in nodes],
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("遍历地理节点失败")
            return json.dumps({"error": f"遍历失败: {e}"}, ensure_ascii=False)
