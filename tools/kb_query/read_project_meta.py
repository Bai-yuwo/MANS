"""
tools/kb_query/read_project_meta.py

读取项目元信息 — workspace/{project_id}/project_meta.json。

返回字段(主要):
    id / name / genre / core_idea / protagonist_seed / target_length /
    tone / style_reference / forbidden_elements / current_chapter / status

使用场景:
    - 主管在 system_prompt 渲染前需要知道项目题材/语气作为对齐基准。
    - Writer 在最初不知道项目背景时,先调一次拉取风格基线。
"""

import json
from pathlib import Path

import aiofiles

from core.base_tool import BaseTool
from core.config import get_config
from core.context import require_current_project_id
from core.logging_config import get_logger

logger = get_logger("tools.kb_query.read_project_meta")


class ReadProjectMeta(BaseTool):
    """读取项目元数据(题材、语气、当前章节等)。"""

    @property
    def name(self) -> str:
        return "read_project_meta"

    @property
    def description(self) -> str:
        return "读取当前项目的元信息(项目名、题材、核心立意、目标字数、当前章节号、状态等)。"

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

        cfg = get_config()
        meta_path = Path(cfg.WORKSPACE_PATH) / pid / "project_meta.json"
        if not meta_path.exists():
            return json.dumps(
                {"error": f"项目元信息不存在: {meta_path}"},
                ensure_ascii=False,
            )
        try:
            async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
                content = await f.read()
            return content  # 直接透传 JSON,避免重复 dump
        except Exception as e:
            logger.exception("读取 project_meta 失败")
            return json.dumps({"error": f"读取失败: {e}"}, ensure_ascii=False)
