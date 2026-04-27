"""
tools/kb_query/read_social_system.py

沿社会制度层级递推遍历。

支持向下（从上级到下级）、向上（从下级到上级）、双向遍历。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.social_db import SocialDB

logger = get_logger("tools.kb_query.read_social_system")


class ReadSocialSystem(BaseTool):
    @property
    def name(self) -> str:
        return "read_social_system"

    @property
    def description(self) -> str:
        return (
            "沿社会制度层级递推遍历。例如：从'皇帝'向下遍历 2 步，"
            "可得到下辖的官僚层级；向上遍历可回溯上级制度。"
            "也可不指定起点，直接获取完整社会体系定义。"
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
                    "from_node_id": {
                        "type": "string",
                        "description": "起始节点 ID，留空则返回完整社会体系",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up", "both"],
                        "description": "遍历方向：down=向下（下级），up=向上（上级），both=双向",
                    },
                    "steps": {
                        "type": "integer",
                        "minimum": -1,
                        "maximum": 20,
                        "description": "递推步数，-1 表示走到尽头，默认 -1",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        from_node_id: str = "",
        direction: str = "down",
        steps: int = -1,
        **kwargs,
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = SocialDB(pid)

            # 如果不指定起点，返回完整社会体系
            if not from_node_id:
                system = await db.get_system()
                if not system:
                    return json.dumps({"error": "社会体系尚未建立"}, ensure_ascii=False)
                subtree = await db.get_subtree(system.root_id)
                return json.dumps(
                    {
                        "system": system.model_dump(),
                        "subtree": subtree,
                    },
                    ensure_ascii=False,
                )

            nodes = await db.traverse_hierarchy(from_node_id, direction, steps)
            return json.dumps(
                {
                    "from_node_id": from_node_id,
                    "direction": direction,
                    "steps": steps,
                    "count": len(nodes),
                    "nodes": [n.model_dump() for n in nodes],
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("读取社会制度失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
