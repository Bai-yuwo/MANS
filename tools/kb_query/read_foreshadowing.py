"""
tools/kb_query/read_foreshadowing.py

读取伏笔。两种模式:
    - 默认:返回所有伏笔条目(供主管/审查类专家全局视图)。
    - active=True:返回当前章节"激活"的伏笔(供 SceneDirector 在节拍表中嵌入)。

数据源:`workspace/{project_id}/foreshadowing/items.json` 通过 `ForeshadowingDB`。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.foreshadowing_db import ForeshadowingDB

logger = get_logger("tools.kb_query.read_foreshadowing")


class ReadForeshadowing(BaseTool):
    @property
    def name(self) -> str:
        return "read_foreshadowing"

    @property
    def description(self) -> str:
        return (
            "读取伏笔列表。默认返回全部;若提供 current_chapter 则只返回该章节"
            "需要关注的活跃伏笔(已按紧急度排序)。"
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
                    "current_chapter": {
                        "type": "integer",
                        "description": "当前章节号。提供后只返回该章节激活的伏笔。",
                    },
                    "trigger_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要显式触发的伏笔 ID 列表(场景规划已指定时填)。",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        current_chapter: int | None = None,
        trigger_ids: list[str] | None = None,
        **kwargs,
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = ForeshadowingDB(pid)
            if current_chapter is not None:
                items = await db.get_active_for_chapter(
                    current_chapter=current_chapter,
                    trigger_ids=trigger_ids or [],
                )
                mode = "active"
            else:
                items = await db.get_all_items()
                mode = "all"
            return json.dumps(
                {
                    "mode": mode,
                    "current_chapter": current_chapter,
                    "count": len(items),
                    "items": [it.model_dump() for it in items],
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("读取 foreshadowing 失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
