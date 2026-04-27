"""
tools/world/append_foreshadowing.py

追加伏笔到 ForeshadowingDB。同样 append-only。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import ForeshadowingItem
from knowledge_bases.foreshadowing_db import ForeshadowingDB

logger = get_logger("tools.world.append_foreshadowing")


class AppendForeshadowing(BaseTool):
    @property
    def name(self) -> str:
        return "append_foreshadowing"

    @property
    def description(self) -> str:
        return (
            "追加一条或多条伏笔。items 是对象数组,每条至少含 type(plot/character/world/emotional)、"
            "description、planted_chapter、trigger_range[start,end]、urgency(low/medium/high)。"
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
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "description": {"type": "string"},
                                "planted_chapter": {"type": "integer"},
                                "trigger_range": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 2,
                                    "maxItems": 2,
                                },
                                "urgency": {"type": "string"},
                            },
                            "required": ["type", "description", "planted_chapter", "trigger_range"],
                            "additionalProperties": True,
                        },
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        }

    async def execute(self, items: list[dict], **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        if not items:
            return json.dumps({"error": "items 不能为空"}, ensure_ascii=False)

        try:
            db = ForeshadowingDB(pid)
            saved = 0
            failed: list[dict] = []
            for raw in items:
                try:
                    # trigger_range 可能是 list,Pydantic v2 会校验为 tuple
                    if isinstance(raw.get("trigger_range"), list):
                        raw = {**raw, "trigger_range": tuple(raw["trigger_range"])}
                    item = ForeshadowingItem(**raw)
                except Exception as e:
                    failed.append({"item": raw, "error": str(e)})
                    continue
                ok = await db.add_item(item)
                if ok:
                    saved += 1
                else:
                    failed.append({"item": raw, "error": "add_item 返回 False"})
            return json.dumps(
                {"saved": saved, "failed": failed, "total": len(items)},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("append_foreshadowing 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
