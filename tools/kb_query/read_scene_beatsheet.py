"""
tools/kb_query/read_scene_beatsheet.py

读取场景节拍表(SceneBeatsheet)。SceneBeatsheet 由 SceneDirector 专家产出,
存于 `workspace/{pid}/chapters/scene_beatsheets/scene_{i}.json`(全局编号)。

设计要点:
    - SceneShowrunner 在调用 Writer 前会先确保节拍表已落盘,Writer 后续仅看节拍表。
    - 全局 scene_index 由 PlotArchitect 在 chapter_plan 中分配。
    - 节拍表落在 chapters/scene_beatsheets/ 子目录,所以这里 db_name 用 "chapters/scene_beatsheets"
      复用 BaseDB 即可,不必新建 SceneBeatsheetDB。
"""

import json

from knowledge_bases.base_db import BaseDB

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger

logger = get_logger("tools.kb_query.read_scene_beatsheet")


class ReadSceneBeatsheet(BaseTool):
    @property
    def name(self) -> str:
        return "read_scene_beatsheet"

    @property
    def description(self) -> str:
        return (
            "读取指定场景的节拍表(感官要求 + 动作节拍 + 情绪节拍)。"
            "Writer 不应直接读 KB 字典,所有设定应通过节拍表呈现。"
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
                    "scene_index": {
                        "type": "integer",
                        "description": "全局场景索引(从 1 开始)。",
                    }
                },
                "required": ["scene_index"],
                "additionalProperties": False,
            },
        }

    async def execute(self, scene_index: int, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = BaseDB(pid, "chapters/scene_beatsheets")
            data = await db.load(f"scene_{scene_index}")
            if data is None:
                return json.dumps(
                    {
                        "error": f"场景 {scene_index} 的节拍表不存在",
                        "scene_index": scene_index,
                    },
                    ensure_ascii=False,
                )
            return json.dumps(data, ensure_ascii=False)
        except Exception as e:
            logger.exception("读取 scene_beatsheet 失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
