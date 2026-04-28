"""
tools/world/save_tech_nodes.py

WorldArchitect 批量写工具 — 一次保存多个科技节点到 TechDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import TechNode
from knowledge_bases.tech_db import TechTreeDB

logger = get_logger("tools.world.save_tech_nodes")


class SaveTechNodes(BaseTool):
    @property
    def name(self) -> str:
        return "save_tech_nodes"

    @property
    def description(self) -> str:
        return (
            "批量保存或更新多个科技节点到知识库。"
            "适用于 WorldArchitect 拿到 RuleSmith 产出的 TechNode[] 后一次性落盘。"
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
                        "description": "科技节点数据列表，每个元素须符合 TechNode 结构",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "节点唯一标识，新建时可为空"},
                                "name": {"type": "string", "description": "技术名称"},
                                "tier": {"type": "integer", "description": "技术等级"},
                                "node_type": {"type": "string", "enum": ["tech", "milestone", "branch", "special"]},
                                "parent_id": {"type": "string", "description": "前置技术 ID"},
                                "next_ids": {"type": "array", "items": {"type": "string"}, "description": "后续技术 ID 列表"},
                                "branch_from": {"type": "string", "description": "从哪个节点分出的分支"},
                                "effects": {"type": "array", "items": {"type": "string"}, "description": "技术效果"},
                                "limitations": {"type": "array", "items": {"type": "string"}, "description": "限制"},
                                "prerequisites": {"type": "array", "items": {"type": "string"}, "description": "研发前置条件"},
                                "research_cost": {"type": "string", "description": "研发代价"},
                                "description": {"type": "string", "description": "技术描述"},
                            },
                            "required": ["name", "tier", "node_type", "description"],
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
        db = TechDB(pid)

        for node_data in nodes:
            try:
                tech_node = TechNode(**node_data)
                ok = await db.save_node(tech_node)
                if ok:
                    results.append({"node_id": tech_node.id, "name": tech_node.name, "success": True})
                else:
                    errors.append({"name": node_data.get("name", ""), "error": "保存失败"})
            except Exception as e:
                logger.exception(f"save_tech_nodes 中节点 {node_data.get('name', '?')} 保存失败")
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
