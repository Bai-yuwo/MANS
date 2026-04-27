"""
tools/style/search_style_examples.py

按情绪基调或题材关键词检索风格示例段落。

数据源:`workspace/{pid}/style/tone_{tone}.json`(StyleDB)。
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.style_db import StyleDB

logger = get_logger("tools.style.search_style_examples")


class SearchStyleExamples(BaseTool):
    @property
    def name(self) -> str:
        return "search_style_examples"

    @property
    def description(self) -> str:
        return (
            "按情绪基调检索风格示例段落(如 '热血'、'压抑'、'温情'、'悬疑')。"
            "返回前 limit 条示例文本。"
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
                    "tone": {
                        "type": "string",
                        "description": "情绪基调名称(如'热血'、'压抑'、'温情'、'悬疑')。",
                    },
                    "scene_type": {
                        "type": "string",
                        "description": "场景类型过滤(如'fight'、'dialogue'、'psychology'、'environment'、'emotional_burst')。为空时不过滤。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "返回数量上限,默认 3。",
                    },
                },
                "required": ["tone"],
                "additionalProperties": False,
            },
        }

    async def execute(self, tone: str, scene_type: str = "", limit: int = 3, **kwargs) -> str:
        if not tone:
            return json.dumps({"error": "tone 不能为空"}, ensure_ascii=False)

        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = StyleDB(pid)
            examples = await db.get_examples_by_tone(
                tone,
                limit=max(1, min(10, int(limit))),
                scene_type=scene_type,
            )
            # 只返回 text 字段给 LLM,减少 token 消耗
            texts = [ex["text"] if isinstance(ex, dict) and "text" in ex else str(ex) for ex in examples]
            return json.dumps(
                {"tone": tone, "scene_type": scene_type, "count": len(texts), "examples": texts},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("search_style_examples 失败")
            return json.dumps({"error": f"检索失败: {e}"}, ensure_ascii=False)
