"""
tools/world/save_faction_nodes.py

WorldArchitect 批量写工具 — 一次保存多个势力节点到 FactionDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import FactionNode
from knowledge_bases.faction_db import FactionDB

logger = get_logger("tools.world.save_faction_nodes")


class SaveFactionNodes(BaseTool):
    @property
    def name(self) -> str:
        return "save_faction_nodes"

    @property
    def description(self) -> str:
        return (
            "批量保存或更新多个势力节点到知识库。"
            "适用于 WorldArchitect 拿到 Geographer 产出的 FactionNode[] 后一次性落盘。"
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
                    "nodes": {
                        "type": "array",
                        "description": "势力节点数据列表，每个元素须符合 FactionNode 结构",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "节点唯一标识，新建时可为空"},
                                "name": {"type": "string", "description": "势力名称"},
                                "node_type": {"type": "string", "enum": ["sect", "dynasty", "guild", "clan", "secret_org", "alliance", "tribe", "council", "corporation", "government", "empire", "federation", "family", "military", "school", "media"]},
                                "stance": {"type": "string", "enum": ["righteous", "neutral", "evil", "gray"]},
                                "description": {"type": "string", "description": "势力描述"},
                                "parent_faction_id": {"type": "string", "description": "上级势力 ID"},
                                "sub_faction_ids": {"type": "array", "items": {"type": "string"}, "description": "下级势力 ID 列表"},
                                "relations": {"type": "array", "items": {"type": "object"}, "description": "与其他势力的关系边"},
                                "controlled_territories": {"type": "array", "items": {"type": "string"}, "description": "控制的地理节点 ID 列表"},
                            },
                            "required": ["name", "node_type", "stance", "description"],
                        },
                    }
                },
                "required": ["nodes"],
                "additionalProperties": False,
            },
        }

    async def execute(self, nodes: list, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        results = []
        errors = []
        db = FactionDB(pid)

        for node_data in nodes:
            try:
                faction_node = FactionNode(**node_data)
                ok = await db.save_node(faction_node)
                if ok:
                    results.append({"node_id": faction_node.id, "name": faction_node.name, "success": True})
                else:
                    errors.append({"name": node_data.get("name", ""), "error": "保存失败"})
            except Exception as e:
                logger.exception(f"save_faction_nodes 中节点 {node_data.get('name', '?')} 保存失败")
                errors.append({"name": node_data.get("name", ""), "error": str(e)})

        return json.dumps(
            {
                "saved_count": len(results),
                "failed_count": len(errors),
                "saved": results,
                "errors": errors,
            },
            ensure_ascii=False,
        )
