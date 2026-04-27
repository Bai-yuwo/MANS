"""
tools/world/save_faction_node.py

WorldArchitect 写工具 — 保存/更新势力节点到 FactionDB。

自动维护层级关系一致性（parent_faction_id ↔ sub_faction_ids 双向引用）。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import FactionNode
from knowledge_bases.faction_db import FactionDB

logger = get_logger("tools.world.save_faction_node")


class SaveFactionNode(BaseTool):
    @property
    def name(self) -> str:
        return "save_faction_node"

    @property
    def description(self) -> str:
        return (
            "保存或更新单个势力节点到知识库。"
            "自动维护 parent_faction_id 与 sub_faction_ids 的双向引用一致性。"
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
                        "description": "势力节点数据，须符合 FactionNode 结构",
                        "properties": {
                            "id": {"type": "string", "description": "节点唯一标识，新建时可为空"},
                            "name": {"type": "string", "description": "势力名称"},
                            "node_type": {"type": "string", "enum": ["sect", "dynasty", "guild", "clan", "secret_org", "alliance", "tribe", "council"]},
                            "stance": {"type": "string", "enum": ["righteous", "neutral", "evil", "gray"]},
                            "parent_faction_id": {"type": "string", "description": "上级势力 ID，无则留空"},
                            "sub_faction_ids": {"type": "array", "items": {"type": "string"}},
                            "description": {"type": "string", "description": "势力描述"},
                            "leader": {"type": "string", "description": "领袖名称"},
                            "relations": {"type": "array", "items": {"type": "object"}, "description": "与其他势力的关系列表"},
                            "controlled_territories": {"type": "array", "items": {"type": "string"}, "description": "控制的地理节点 ID 列表"},
                            "member_count_estimate": {"type": "string"},
                            "founded_chapter": {"type": "integer"},
                        },
                        "required": ["name", "node_type", "stance", "description"],
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
            faction_node = FactionNode(**node)
            db = FactionDB(pid)
            ok = await db.save_node(faction_node)
            if ok:
                return json.dumps(
                    {"success": True, "node_id": faction_node.id, "name": faction_node.name},
                    ensure_ascii=False,
                )
            return json.dumps({"error": "保存失败"}, ensure_ascii=False)
        except Exception as e:
            logger.exception("save_faction_node 失败")
            return json.dumps({"error": f"保存失败: {e}"}, ensure_ascii=False)
