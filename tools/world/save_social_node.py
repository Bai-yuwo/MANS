"""
tools/world/save_social_node.py

WorldArchitect 写工具 — 保存/更新社会制度节点到 SocialDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import SocialNode
from knowledge_bases.social_db import SocialDB

logger = get_logger("tools.world.save_social_node")


class SaveSocialNode(BaseTool):
    @property
    def name(self) -> str:
        return "save_social_node"

    @property
    def description(self) -> str:
        return (
            "保存或更新单个社会制度/阶层节点到知识库。"
            "用 parent_id/sub_ids 表达层级关系。"
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
                        "description": "社会节点数据，须符合 SocialNode 结构",
                        "properties": {
                            "id": {"type": "string", "description": "节点唯一标识，新建时可为空"},
                            "name": {"type": "string", "description": "节点名称"},
                            "node_type": {"type": "string", "enum": ["class", "institution", "law", "tradition", "role"]},
                            "parent_id": {"type": "string", "description": "上级阶层/制度 ID"},
                            "sub_ids": {"type": "array", "items": {"type": "string"}, "description": "下级/子制度 ID 列表"},
                            "description": {"type": "string", "description": "节点描述"},
                            "influence_scope": {"type": "string", "description": "影响范围"},
                            "privileges": {"type": "array", "items": {"type": "string"}, "description": "特权/权利"},
                            "obligations": {"type": "array", "items": {"type": "string"}, "description": "义务/约束"},
                            "related_faction_ids": {"type": "array", "items": {"type": "string"}, "description": "关联势力 ID 列表"},
                        },
                        "required": ["name", "node_type", "description"],
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
            social_node = SocialNode(**node)
            db = SocialDB(pid)
            ok = await db.save_node(social_node)
            if ok:
                return json.dumps(
                    {"success": True, "node_id": social_node.id, "name": social_node.name},
                    ensure_ascii=False,
                )
            return json.dumps({"error": "保存失败"}, ensure_ascii=False)
        except Exception as e:
            logger.exception("save_social_node 失败")
            return json.dumps({"error": f"保存失败: {e}"}, ensure_ascii=False)
