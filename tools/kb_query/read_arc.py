"""
tools/kb_query/read_arc.py

读取故事弧规划。

数据源:`workspace/{project_id}/arcs/arc_{arc_id}.json` 通过 `StoryDB`。
两种调用形态:
    - arc_id="arc_3":精确读取
    - chapter_number=15:按章节号反查所属弧线
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.kb_query.read_arc")


class ReadArc(BaseTool):
    @property
    def name(self) -> str:
        return "read_arc"

    @property
    def description(self) -> str:
        return (
            "读取故事弧规划。提供 arc_id 精确读取;提供 chapter_number 按章节反查所属弧。"
            "二选一,均不传则返回错误。"
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
                    "arc_id": {
                        "type": "string",
                        "description": "弧线唯一 ID(如 'arc_1')。",
                    },
                    "chapter_number": {
                        "type": "integer",
                        "description": "章节号,用于反查所属弧。",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        arc_id: str | None = None,
        chapter_number: int | None = None,
        **kwargs,
    ) -> str:
        if arc_id is None and chapter_number is None:
            return json.dumps(
                {"error": "arc_id 与 chapter_number 至少传一个"},
                ensure_ascii=False,
            )
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = StoryDB(pid)
            if arc_id is not None:
                data = await db.get_arc_plan(arc_id)
                if data is None:
                    return json.dumps(
                        {"error": f"弧线不存在: {arc_id}"},
                        ensure_ascii=False,
                    )
                return json.dumps({"arc": data}, ensure_ascii=False)

            data = await db.get_arc_plan_for_chapter(chapter_number)
            if data is None:
                return json.dumps(
                    {
                        "error": f"未找到包含章节 {chapter_number} 的弧线",
                        "chapter_number": chapter_number,
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"arc": data}, ensure_ascii=False)
        except Exception as e:
            logger.exception("读取 arc 失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
