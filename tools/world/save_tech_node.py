"""
tools/world/save_tech_node.py

WorldArchitect 写工具 — 保存/更新科技节点到 TechTreeDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import TechNode
from knowledge_bases.tech_db import TechTreeDB

logger = get_logger("tools.world.save_tech_node")


class SaveTechNode(BaseTool):
    @property
    def name(self) -> str:
        return "save_tech_node"

    @property
    def description(self) -> str:
        return (
            "保存或更新单个科技/技术节点到知识库。"
            "用 parent_id/next_ids 表达技术递进关系，branch_from 表达技术分支。"
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
                        "description": "科技节点数据，须符合 TechNode 结构",
                        "properties": {
                            "id": {"type": "string", "description": "节点唯一标识，新建时可为空"},
                            "name": {"type": "string", "description": "技术名称"},
                            "tier": {"type": "integer", "description": "技术等级序号（越小越低）"},
                            "node_type": {"type": "string", "enum": ["tech", "milestone", "branch", "special"]},
                            "parent_id": {"type": "string", "description": "前置技术 ID"},
                            "next_ids": {"type": "array", "items": {"type": "string"}, "description": "后续技术 ID 列表"},
                            "branch_from": {"type": "string", "description": "从哪个节点分出的分支"},
                            "prerequisites": {"type": "array", "items": {"type": "string"}, "description": "研发前置条件"},
                            "effects": {"type": "array", "items": {"type": "string"}, "description": "技术效果/应用"},
                            "limitations": {"type": "array", "items": {"type": "string"}, "description": "技术限制/副作用"},
                            "research_cost": {"type": "string", "description": "研发代价（时间/资源/人力）"},
                            "description": {"type": "string", "description": "技术描述"},
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
            tech_node = TechNode(**node)
            db = TechTreeDB(pid)
            ok = await db.save_node(tech_node)
            if ok:
                return json.dumps(
                    {"success": True, "node_id": tech_node.id, "name": tech_node.name},
                    ensure_ascii=False,
                )
            return json.dumps({"error": "保存失败"}, ensure_ascii=False)
        except Exception as e:
            logger.exception("save_tech_node 失败")
            return json.dumps({"error": f"保存失败: {e}"}, ensure_ascii=False)
