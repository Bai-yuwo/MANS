"""
tools/kb_query/read_setting.py

查询通用设定节点。

支持按 ID、名称、分类、重要性查询。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.setting_db import SettingDB

logger = get_logger("tools.kb_query.read_setting")


class ReadSetting(BaseTool):
    @property
    def name(self) -> str:
        return "read_setting"

    @property
    def description(self) -> str:
        return (
            "查询通用设定节点。可按分类（magic/physics/economy/culture/custom/other）"
            "或重要性（critical/major/minor）过滤，也可查询单个节点详情。"
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
                    "node_id": {
                        "type": "string",
                        "description": "节点 ID，留空则按分类/重要性过滤查询",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["magic", "physics", "economy", "culture", "custom", "other"],
                        "description": "按分类过滤",
                    },
                    "importance": {
                        "type": "string",
                        "enum": ["critical", "major", "minor"],
                        "description": "按重要性过滤",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        node_id: str = "",
        category: str = "",
        importance: str = "",
        **kwargs,
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = SettingDB(pid)

            # 按 ID 查询单个节点
            if node_id:
                node = await db.get_node(node_id)
                if not node:
                    return json.dumps({"error": f"节点不存在: {node_id}"}, ensure_ascii=False)
                return json.dumps(
                    {"node": node.model_dump()},
                    ensure_ascii=False,
                )

            # 按分类查询
            if category:
                nodes = await db.list_by_category(category)
                return json.dumps(
                    {
                        "category": category,
                        "count": len(nodes),
                        "nodes": [n.model_dump() for n in nodes],
                    },
                    ensure_ascii=False,
                )

            # 按重要性查询
            if importance:
                nodes = await db.list_by_importance(importance)
                return json.dumps(
                    {
                        "importance": importance,
                        "count": len(nodes),
                        "nodes": [n.model_dump() for n in nodes],
                    },
                    ensure_ascii=False,
                )

            # 查询所有
            nodes = await db.list_all_nodes()
            return json.dumps(
                {
                    "count": len(nodes),
                    "nodes": [n.model_dump() for n in nodes],
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("读取设定节点失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
