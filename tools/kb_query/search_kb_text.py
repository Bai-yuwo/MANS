"""
tools/kb_query/search_kb_text.py

文本子串检索(非向量)— 在指定 KB 类别下做精确字符串包含匹配。
用途:LLM 想做"哪些规则提到了'御剑飞行'"这类关键词查询时,
向量检索可能误召回近义但不相同的内容,文本检索更稳。

支持的 kb_kind 与对应数据源:
    bible          -> BibleDB.get_rules() 全量
    foreshadowing  -> ForeshadowingDB.get_all_items() 全量
    characters     -> CharacterDB.list_all_characters() 全量字典
    outline        -> StoryDB.get_outline() 字典(深度遍历字符串字段)
    chapter_plan   -> 全部章节规划(深度遍历)
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.bible_db import BibleDB
from knowledge_bases.character_db import CharacterDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.kb_query.search_kb_text")

ALLOWED_KINDS = ["bible", "foreshadowing", "characters", "outline", "chapter_plan"]


def _walk_strings(node, path: str = ""):
    """递归遍历 dict/list,yield (path, str_value)。"""
    if isinstance(node, str):
        if node.strip():
            yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_strings(v, f"{path}[{i}]")


class SearchKBText(BaseTool):
    @property
    def name(self) -> str:
        return "search_kb_text"

    @property
    def description(self) -> str:
        return (
            "在指定 KB 类别下做文本子串检索(非向量)。"
            "返回匹配条目的位置路径与原文片段,适合关键词精确召回。"
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
                    "kb_kind": {
                        "type": "string",
                        "enum": ALLOWED_KINDS,
                        "description": "要检索的 KB 类别。",
                    },
                    "query": {
                        "type": "string",
                        "description": "要查找的子串(精确包含匹配)。",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写,默认 false。",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "返回结果上限,默认 20。",
                    },
                },
                "required": ["kb_kind", "query"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        kb_kind: str,
        query: str,
        case_sensitive: bool = False,
        max_results: int = 20,
        **kwargs,
    ) -> str:
        if kb_kind not in ALLOWED_KINDS:
            return json.dumps(
                {"error": f"非法 kb_kind: {kb_kind}", "allowed": ALLOWED_KINDS},
                ensure_ascii=False,
            )
        if not query.strip():
            return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)

        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        max_results = max(1, min(50, int(max_results)))
        needle = query if case_sensitive else query.lower()

        try:
            data_root: object = None
            if kb_kind == "bible":
                rules = await BibleDB(pid).get_rules()
                data_root = [r.model_dump() for r in rules]
            elif kb_kind == "foreshadowing":
                items = await ForeshadowingDB(pid).get_all_items()
                data_root = [it.model_dump() for it in items]
            elif kb_kind == "characters":
                data_root = await CharacterDB(pid).list_all_characters()
            elif kb_kind == "outline":
                data_root = await StoryDB(pid).get_outline() or {}
            elif kb_kind == "chapter_plan":
                story = StoryDB(pid)
                keys = await story.list_keys()
                merged: dict = {}
                for k in keys:
                    if k.endswith("_plan"):
                        d = await story.load(k)
                        if d:
                            merged[k] = d
                data_root = merged

            matches: list[dict] = []
            for path, text in _walk_strings(data_root):
                hay = text if case_sensitive else text.lower()
                if needle in hay:
                    matches.append({"path": path, "snippet": text[:200]})
                    if len(matches) >= max_results:
                        break

            return json.dumps(
                {
                    "kb_kind": kb_kind,
                    "query": query,
                    "count": len(matches),
                    "matches": matches,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("文本检索失败")
            return json.dumps({"error": f"检索失败: {e}"}, ensure_ascii=False)
