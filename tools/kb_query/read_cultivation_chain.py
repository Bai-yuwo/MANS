"""
tools/kb_query/read_cultivation_chain.py

沿修为链条递推遍历。

支持正向（从低到高）、反向（从高到低）、双向遍历。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.cultivation_db import CultivationDB

logger = get_logger("tools.kb_query.read_cultivation_chain")


class ReadCultivationChain(BaseTool):
    @property
    def name(self) -> str:
        return "read_cultivation_chain"

    @property
    def description(self) -> str:
        return (
            "沿修为链条递推遍历。例如：从'龙血觉醒'正向遍历 3 步，"
            "可得到后续 3 个境界；反向遍历可回溯上级境界。"
            "也可不指定起点，直接获取完整修为体系定义。"
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
                        "description": "起始节点 ID，留空则返回完整修为体系",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "backward", "both"],
                        "description": "遍历方向：forward=向后（高级），backward=向前（低级），both=双向",
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
        direction: str = "forward",
        steps: int = -1,
        **kwargs,
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = CultivationDB(pid)

            # 如果不指定起点，返回完整修为体系
            if not from_node_id:
                chain = await db.get_chain()
                if not chain:
                    return json.dumps({"error": "修为体系尚未建立"}, ensure_ascii=False)
                full_tree = await db.get_full_chain(chain.root_id)
                return json.dumps(
                    {
                        "chain": chain.model_dump(),
                        "full_tree": full_tree,
                    },
                    ensure_ascii=False,
                )

            nodes = await db.traverse_chain(from_node_id, direction, steps)
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
            logger.exception("读取修为链失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
