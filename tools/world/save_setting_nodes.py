"""
tools/world/save_setting_nodes.py

WorldArchitect 批量写工具 — 一次保存多个通用设定节点到 SettingDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import SettingNode
from knowledge_bases.setting_db import SettingDB

logger = get_logger("tools.world.save_setting_nodes")


class SaveSettingNodes(BaseTool):
    @property
    def name(self) -> str:
        return "save_setting_nodes"

    @property
    def description(self) -> str:
        return (
            "批量保存或更新多个通用设定节点到知识库。"
            "适用于 WorldArchitect 拿到 RuleSmith 产出的 SettingNode[] 后一次性落盘。"
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
                        "description": "通用设定节点数据列表，每个元素须符合 SettingNode 结构",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "节点唯一标识，新建时可为空"},
                                "name": {"type": "string", "description": "设定名称"},
                                "category": {"type": "string", "enum": ["magic", "physics", "economy", "culture", "custom", "other"]},
                                "importance": {"type": "string", "enum": ["critical", "major", "minor"]},
                                "description": {"type": "string", "description": "设定描述"},
                                "related_node_ids": {"type": "array", "items": {"type": "string"}, "description": "关联的其他节点 ID 列表"},
                            },
                            "required": ["name", "category", "description"],
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
        db = SettingDB(pid)

        for node_data in nodes:
            try:
                setting_node = SettingNode(**node_data)
                ok = await db.save_node(setting_node)
                if ok:
                    results.append({"node_id": setting_node.id, "name": setting_node.name, "success": True})
                else:
                    errors.append({"name": node_data.get("name", ""), "error": "保存失败"})
            except Exception as e:
                logger.exception(f"save_setting_nodes 中节点 {node_data.get('name', '?')} 保存失败")
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
