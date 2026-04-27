"""
tools/kb_query/list_chapters.py

列出项目下所有已规划/已完稿的章节。

返回:
    {
        "planned":  [1, 2, 3, ...],   # 有 chapter_n_plan.json 的章节
        "drafted":  [1, 2, ...],      # 有 chapter_n_draft.json
        "finalized":[1, 2, ...]       # chapters/ 目录下有 chapter_n_final.json
    }
"""

import json
import re
from pathlib import Path

from core.base_tool import BaseTool
from core.config import get_config
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.story_db import StoryDB

logger = get_logger("tools.kb_query.list_chapters")


class ListChapters(BaseTool):
    @property
    def name(self) -> str:
        return "list_chapters"

    @property
    def description(self) -> str:
        return "列出项目下所有章节的规划/草稿/终稿状态(三个数组)。"

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(self, **kwargs) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = StoryDB(pid)
            keys = await db.list_keys()
            planned: list[int] = []
            drafted: list[int] = []
            for k in keys:
                m = re.match(r"chapter_(\d+)_plan$", k)
                if m:
                    planned.append(int(m.group(1)))
                    continue
                m = re.match(r"chapter_(\d+)_draft$", k)
                if m:
                    drafted.append(int(m.group(1)))

            cfg = get_config()
            chapters_dir = Path(cfg.WORKSPACE_PATH) / pid / "chapters"
            finalized: list[int] = []
            if chapters_dir.exists():
                for p in chapters_dir.glob("chapter_*_final.json"):
                    m = re.match(r"chapter_(\d+)_final", p.stem)
                    if m:
                        finalized.append(int(m.group(1)))

            return json.dumps(
                {
                    "planned": sorted(set(planned)),
                    "drafted": sorted(set(drafted)),
                    "finalized": sorted(set(finalized)),
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("列出章节失败")
            return json.dumps({"error": f"列出失败: {e}"}, ensure_ascii=False)
