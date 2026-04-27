"""
tools/system/apply_kb_diff.py

SceneShowrunner 接收 Scribe 产物后的 KB 增量应用。

KB diff 形态(由 Scribe 输出):
    {
        "characters": [
            {"name": "...", "patch": {...}}
        ],
        "foreshadowing": {
            "add":     [ {ForeshadowingItem fields...} ],
            "update":  [ {"id": "...", "status": "...", "notes": "..."} ]
        },
        "bible": {
            "add": [ {WorldRule fields...} ]
        }
    }

工具按字段分发到对应 BaseDB 子类。失败的部分会单独列出,而不是整体回滚——
KB 是 append-only 主体,部分写入失败影响范围有限。
"""

import json
from typing import Any

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.schemas import ForeshadowingItem, WorldRule
from knowledge_bases.bible_db import BibleDB
from knowledge_bases.character_db import CharacterDB
from knowledge_bases.foreshadowing_db import ForeshadowingDB

logger = get_logger("tools.system.apply_kb_diff")


class ApplyKBDiff(BaseTool):
    @property
    def name(self) -> str:
        return "apply_kb_diff"

    @property
    def description(self) -> str:
        return (
            "把 Scribe 产出的 KB diff 落到对应知识库。支持 characters/foreshadowing/bible 三个区块,"
            "characters 中每项含 name + patch(深度合并),foreshadowing.add/update 与 bible.add 为数组。"
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
                    "diff": {
                        "type": "object",
                        "properties": {
                            "characters": {"type": "array"},
                            "foreshadowing": {"type": "object"},
                            "bible": {"type": "object"},
                        },
                        "additionalProperties": True,
                    }
                },
                "required": ["diff"],
                "additionalProperties": False,
            },
        }

    async def execute(self, diff: dict, **kwargs) -> str:
        if not isinstance(diff, dict):
            return json.dumps({"error": "diff 必须是对象"}, ensure_ascii=False)

        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        result: dict[str, Any] = {
            "characters": {"updated": 0, "failed": []},
            "foreshadowing": {"added": 0, "updated": 0, "failed": []},
            "bible": {"added": 0, "failed": []},
        }

        # 1. characters: 按 name + patch 进行 BaseDB.save 深度合并
        char_db = CharacterDB(pid)
        for entry in diff.get("characters", []) or []:
            try:
                name = entry.get("name")
                patch = entry.get("patch") or {}
                if not name or not isinstance(patch, dict):
                    raise ValueError("entry 缺少 name 或 patch")
                ok = await char_db.save(name, patch)
                if ok:
                    result["characters"]["updated"] += 1
                else:
                    result["characters"]["failed"].append({"entry": entry})
            except Exception as e:
                result["characters"]["failed"].append(
                    {"entry": entry, "error": str(e)}
                )

        # 2. foreshadowing.add / update
        fs_block = diff.get("foreshadowing") or {}
        fs_db = ForeshadowingDB(pid)
        for raw in fs_block.get("add", []) or []:
            try:
                if isinstance(raw.get("trigger_range"), list):
                    raw = {**raw, "trigger_range": tuple(raw["trigger_range"])}
                item = ForeshadowingItem(**raw)
                if await fs_db.add_item(item):
                    result["foreshadowing"]["added"] += 1
            except Exception as e:
                result["foreshadowing"]["failed"].append(
                    {"add": raw, "error": str(e)}
                )
        for u in fs_block.get("update", []) or []:
            try:
                fs_id = u.get("id")
                new_status = u.get("status")
                if not fs_id or not new_status:
                    raise ValueError("update 必须含 id 与 status")
                ok = await fs_db.update_status(
                    fs_id=fs_id,
                    new_status=new_status,
                    notes=u.get("notes", ""),
                    triggered_chapter=u.get("triggered_chapter", 0),
                )
                if ok:
                    result["foreshadowing"]["updated"] += 1
                else:
                    result["foreshadowing"]["failed"].append({"update": u})
            except Exception as e:
                result["foreshadowing"]["failed"].append(
                    {"update": u, "error": str(e)}
                )

        # 3. bible.add
        bible_block = diff.get("bible") or {}
        bible_db = BibleDB(pid)
        for raw in bible_block.get("add", []) or []:
            try:
                rule = WorldRule(**raw)
                if await bible_db.append_rule(rule):
                    result["bible"]["added"] += 1
            except Exception as e:
                result["bible"]["failed"].append({"add": raw, "error": str(e)})

        return json.dumps(result, ensure_ascii=False)
