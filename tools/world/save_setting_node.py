"""
tools/world/save_setting_node.py

WorldArchitect 写工具 — 保存/更新通用设定节点到 SettingDB。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import SettingNode
from knowledge_bases.setting_db import SettingDB

logger = get_logger("tools.world.save_setting_node")


class SaveSettingNode(BaseTool):
    @property
    def name(self) -> str:
        return "save_setting_node"

    @property
    def description(self) -> str:
        return (
            "保存或更新单个通用设定节点到知识库。"
            "用于不便归入 Cultivation/Geo/Faction/Tech/Social 的零散设定。"
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
                        "description": "设定节点数据，须符合 SettingNode 结构",
                        "properties": {
                            "id": {"type": "string", "description": "节点唯一标识，新建时可为空"},
                            "name": {"type": "string", "description": "设定名称"},
                            "category": {"type": "string", "enum": ["magic", "physics", "economy", "culture", "custom", "other"]},
                            "description": {"type": "string", "description": "设定描述"},
                            "importance": {"type": "string", "enum": ["critical", "major", "minor"]},
                            "related_node_ids": {"type": "array", "items": {"type": "string"}, "description": "关联的其他节点 ID 列表"},
                        },
                        "required": ["name", "category", "description"],
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
            setting_node = SettingNode(**node)
            db = SettingDB(pid)
            ok = await db.save_node(setting_node)
            if ok:
                return json.dumps(
                    {"success": True, "node_id": setting_node.id, "name": setting_node.name},
                    ensure_ascii=False,
                )
            return json.dumps({"error": "保存失败"}, ensure_ascii=False)
        except Exception as e:
            logger.exception("save_setting_node 失败")
            return json.dumps({"error": f"保存失败: {e}"}, ensure_ascii=False)
