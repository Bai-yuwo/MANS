"""
tools/world/save_geo_node.py

WorldArchitect 写工具 — 保存/更新地理节点到 GeoDB。

自动维护层级关系一致性（parent_id ↔ child_ids 双向引用）。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import GeoNode
from knowledge_bases.geo_db import GeoDB

logger = get_logger("tools.world.save_geo_node")


class SaveGeoNode(BaseTool):
    @property
    def name(self) -> str:
        return "save_geo_node"

    @property
    def description(self) -> str:
        return (
            "保存或更新单个地理节点到知识库。"
            "自动维护 parent_id 与 child_ids 的双向引用一致性。"
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
                        "description": "地理节点数据，须符合 GeoNode 结构",
                        "properties": {
                            "id": {"type": "string", "description": "节点唯一标识，新建时可为空（自动生成）"},
                            "name": {"type": "string", "description": "地点名称"},
                            "node_type": {"type": "string", "enum": ["continent", "region", "state", "city", "district", "site", "realm", "secret_realm"]},
                            "parent_id": {"type": "string", "description": "上级区域节点 ID，无上级则留空"},
                            "child_ids": {"type": "array", "items": {"type": "string"}, "description": "直接下级节点 ID 列表"},
                            "connections": {"type": "array", "items": {"type": "object"}, "description": "空间连接关系列表"},
                            "description": {"type": "string", "description": "地点描述"},
                            "faction_presence": {"type": "array", "items": {"type": "object"}, "description": "势力分布列表"},
                            "depth_level": {"type": "integer", "description": "层级深度"},
                            "scale": {"type": "string", "description": "规模描述"},
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
            geo_node = GeoNode(**node)
            db = GeoDB(pid)
            ok = await db.save_node(geo_node)
            if ok:
                return json.dumps(
                    {"success": True, "node_id": geo_node.id, "name": geo_node.name},
                    ensure_ascii=False,
                )
            return json.dumps({"error": "保存失败"}, ensure_ascii=False)
        except Exception as e:
            logger.exception("save_geo_node 失败")
            return json.dumps({"error": f"保存失败: {e}"}, ensure_ascii=False)
