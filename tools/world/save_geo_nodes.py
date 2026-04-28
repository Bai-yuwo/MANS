"""
tools/world/save_geo_nodes.py

WorldArchitect 批量写工具 — 一次保存多个地理节点到 GeoDB。

自动维护层级关系一致性（parent_id ↔ child_ids 双向引用）。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import GeoNode
from knowledge_bases.geo_db import GeoDB

logger = get_logger("tools.world.save_geo_nodes")


class SaveGeoNodes(BaseTool):
    @property
    def name(self) -> str:
        return "save_geo_nodes"

    @property
    def description(self) -> str:
        return (
            "批量保存或更新多个地理节点到知识库。"
            "适用于 WorldArchitect 拿到 Geographer 产出的 GeoNode[] 后一次性落盘。"
            "自动维护 parent_id 与 child_ids 的双向引用一致性。"
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
                        "description": "地理节点数据列表，每个元素须符合 GeoNode 结构",
                        "items": {
                            "type": "object",
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
        db = GeoDB(pid)

        for node_data in nodes:
            try:
                geo_node = GeoNode(**node_data)
                ok = await db.save_node(geo_node)
                if ok:
                    results.append({"node_id": geo_node.id, "name": geo_node.name, "success": True})
                else:
                    errors.append({"name": node_data.get("name", ""), "error": "保存失败"})
            except Exception as e:
                logger.exception(f"save_geo_nodes 中节点 {node_data.get('name', '?')} 保存失败")
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
