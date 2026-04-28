"""
tools/world/save_cultivation_nodes.py

WorldArchitect 批量写工具 — 一次保存多个修为节点到 CultivationDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import CultivationNode
from knowledge_bases.cultivation_db import CultivationDB

logger = get_logger("tools.world.save_cultivation_nodes")


class SaveCultivationNodes(BaseTool):
    @property
    def name(self) -> str:
        return "save_cultivation_nodes"

    @property
    def description(self) -> str:
        return (
            "批量保存或更新多个修为节点到知识库。"
            "适用于 WorldArchitect 拿到 RuleSmith 产出的 CultivationNode[] 后一次性落盘。"
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
                        "description": "修为节点数据列表，每个元素须符合 CultivationNode 结构",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "节点唯一标识，新建时可为空"},
                                "name": {"type": "string", "description": "境界名称"},
                                "tier": {"type": "integer", "description": "层级序号（越小越低）"},
                                "node_type": {"type": "string", "enum": ["realm", "stage", "breakthrough", "branch", "special"]},
                                "parent_id": {"type": "string", "description": "上级境界 ID"},
                                "next_ids": {"type": "array", "items": {"type": "string"}, "description": "后续境界 ID 列表"},
                                "branch_from": {"type": "string", "description": "从哪个节点分出的分支"},
                                "prerequisites": {"type": "array", "items": {"type": "string"}, "description": "突破前置条件"},
                                "abilities": {"type": "array", "items": {"type": "string"}, "description": "该境界可获得的能力"},
                                "limitations": {"type": "array", "items": {"type": "string"}, "description": "限制与代价"},
                                "power_scale": {"type": "integer", "description": "战力标尺（相对值）"},
                                "description": {"type": "string", "description": "境界描述"},
                            },
                            "required": ["name", "tier", "description"],
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
        db = CultivationDB(pid)

        for node_data in nodes:
            try:
                cul_node = CultivationNode(**node_data)
                ok = await db.save_node(cul_node)
                if ok:
                    results.append({"node_id": cul_node.id, "name": cul_node.name, "success": True})
                else:
                    errors.append({"name": node_data.get("name", ""), "error": "保存失败"})
            except Exception as e:
                logger.exception(f"save_cultivation_nodes 中节点 {node_data.get('name', '?')} 保存失败")
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
