"""
tools/kb_query/vector_search.py

语义向量检索 — 通过 ChromaDB + bge-m3 在指定 collection 中按语义相似度检索。

支持的 collections(详见 vector_store/store.py 文档):
    bible_rules / character_cards / chapter_scenes /
    style_examples / foreshadowing
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from vector_store.store import VectorStore

logger = get_logger("tools.kb_query.vector_search")

ALLOWED_COLLECTIONS = [
    "bible_rules",
    "character_cards",
    "chapter_scenes",
    "style_examples",
    "foreshadowing",
    "geo_nodes",
    "faction_nodes",
    "cultivation_nodes",
]


class VectorSearch(BaseTool):
    @property
    def name(self) -> str:
        return "vector_search"

    @property
    def description(self) -> str:
        return (
            "语义向量检索(bge-m3 + ChromaDB)。在指定 collection 中按相似度返回 top-N 结果。"
            "用于跨知识库的灵感检索,例如查找与当前场景情绪相似的历史场景,或主角语气相近的范例段落。"
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
                    "collection": {
                        "type": "string",
                        "enum": ALLOWED_COLLECTIONS,
                        "description": "目标 collection。",
                    },
                    "query": {
                        "type": "string",
                        "description": "自然语言查询(无需关键词精确匹配)。",
                    },
                    "n_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "返回数量上限,默认 5。",
                    },
                },
                "required": ["collection", "query"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        collection: str,
        query: str,
        n_results: int = 5,
        **kwargs,
    ) -> str:
        if collection not in ALLOWED_COLLECTIONS:
            return json.dumps(
                {
                    "error": f"非法 collection: {collection}",
                    "allowed": ALLOWED_COLLECTIONS,
                },
                ensure_ascii=False,
            )
        if not query.strip():
            return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)

        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            store = VectorStore(pid)
            results = await store.search(
                collection=collection,
                query=query,
                n_results=max(1, min(20, int(n_results))),
            )
            return json.dumps(
                {
                    "collection": collection,
                    "query": query,
                    "count": len(results),
                    "results": results,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("向量检索失败")
            return json.dumps({"error": f"检索失败: {e}"}, ensure_ascii=False)
