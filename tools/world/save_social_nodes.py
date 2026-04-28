"""
tools/world/save_social_nodes.py

WorldArchitect 批量写工具 — 一次保存多个社会制度节点到 SocialDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import SocialNode
from knowledge_bases.social_db import SocialDB

logger = get_logger("tools.world.save_social_nodes")


class SaveSocialNodes(BaseTool):
    @property
    def name(self) -> str:
        return "save_social_nodes"

    @property
    def description(self) -> str:
        return (
            "批量保存或更新多个社会制度节点到知识库。"
            "适用于 WorldArchitect 拿到 RuleSmith 产出的 SocialNode[] 后一次性落盘。"
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
                        "description": "社会制度节点数据列表，每个元素须符合 SocialNode 结构",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "节点唯一标识，新建时可为空"},
                                "name": {"type": "string", "description": "制度/阶层名称"},
                                "node_type": {"type": "string", "enum": ["class", "institution", "law", "tradition", "role"]},
                                "parent_id": {"type": "string", "description": "上级阶层/制度 ID"},
                                "sub_ids": {"type": "array", "items": {"type": "string"}, "description": "下级/子制度 ID 列表"},
                                "privileges": {"type": "array", "items": {"type": "string"}, "description": "特权"},
                                "obligations": {"type": "array", "items": {"type": "string"}, "description": "义务"},
                                "influence_scope": {"type": "string", "description": "影响范围"},
                                "description": {"type": "string", "description": "制度描述"},
                            },
                            "required": ["name", "node_type", "description"],
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
        db = SocialDB(pid)

        for node_data in nodes:
            try:
                social_node = SocialNode(**node_data)
                ok = await db.save_node(social_node)
                if ok:
                    results.append({"node_id": social_node.id, "name": social_node.name, "success": True})
                else:
                    errors.append({"name": node_data.get("name", ""), "error": "保存失败"})
            except Exception as e:
                logger.exception(f"save_social_nodes 中节点 {node_data.get('name', '?')} 保存失败")
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
