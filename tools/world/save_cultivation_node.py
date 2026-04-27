"""
tools/world/save_cultivation_node.py

WorldArchitect 写工具 — 保存/更新修为节点到 CultivationDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import CultivationNode
from knowledge_bases.cultivation_db import CultivationDB

logger = get_logger("tools.world.save_cultivation_node")


class SaveCultivationNode(BaseTool):
    @property
    def name(self) -> str:
        return "save_cultivation_node"

    @property
    def description(self) -> str:
        return (
            "保存或更新单个修为节点到知识库。"
            "用 parent_id/next_ids 表达境界递进关系，branch_from 表达分支。"
            "节点数据变更后会自动同步到向量库。"
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
                    "node": {
                        "type": "object",
                        "description": "修为节点数据，须符合 CultivationNode 结构",
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
                    }
                },
                "required": ["node"],
                "additionalProperties": False,
            },
        }

    async def execute(self, node: dict, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            cul_node = CultivationNode(**node)
            db = CultivationDB(pid)
            ok = await db.save_node(cul_node)
            if ok:
                return json.dumps(
                    {"success": True, "node_id": cul_node.id, "name": cul_node.name},
                    ensure_ascii=False,
                )
            return json.dumps({"error": "保存失败"}, ensure_ascii=False)
        except Exception as e:
            logger.exception("save_cultivation_node 失败")
            return json.dumps({"error": f"保存失败: {e}"}, ensure_ascii=False)
