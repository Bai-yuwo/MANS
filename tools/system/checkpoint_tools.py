"""
tools/system/checkpoint_tools.py

断点续接工具组 — 供 SceneShowrunner 查询和清理 checkpoint。

设计:
    - read_checkpoint:  SceneShowrunner 启动时调用,获取已完成的步骤列表
    - clear_checkpoint: 场景流水线完成后调用,清理已完成的 checkpoint
"""

import json

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from knowledge_bases.checkpoint_db import SceneShowrunnerCheckpointDB

logger = get_logger("tools.system.checkpoint")


class ReadCheckpoint(BaseTool):
    """读取 SceneShowrunner 的断点续接状态。"""

    @property
    def name(self) -> str:
        return "read_checkpoint"

    @property
    def description(self) -> str:
        return (
            "读取指定场景的 checkpoint,返回已完成的流水线步骤列表。"
            "SceneShowrunner 启动时应先调用此工具,根据 completed_steps 跳过已完成的步骤。"
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
                    "chapter_number": {
                        "type": "integer",
                        "description": "章节号",
                    },
                    "scene_index": {
                        "type": "integer",
                        "description": "场景索引(从0开始)",
                    },
                },
                "required": ["chapter_number", "scene_index"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self, chapter_number: int, scene_index: int, **kwargs
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = SceneShowrunnerCheckpointDB(pid)
            cp = await db.get_checkpoint(chapter_number, scene_index)
            steps = list(cp.get("steps", {}).keys())
            return json.dumps(
                {
                    "chapter_number": chapter_number,
                    "scene_index": scene_index,
                    "completed_steps": steps,
                    "has_checkpoint": bool(steps),
                    "checkpoint": cp,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("read_checkpoint 失败")
            return json.dumps(
                {"error": f"读取 checkpoint 失败: {e}"},
                ensure_ascii=False,
            )


class ClearCheckpoint(BaseTool):
    """清理 SceneShowrunner 的 checkpoint(场景完成后调用)。"""

    @property
    def name(self) -> str:
        return "clear_checkpoint"

    @property
    def description(self) -> str:
        return (
            "清理指定场景的 checkpoint。当场景流水线全部完成后调用,"
            "释放不再需要的续接状态。"
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
                    "chapter_number": {
                        "type": "integer",
                        "description": "章节号",
                    },
                    "scene_index": {
                        "type": "integer",
                        "description": "场景索引(从0开始)",
                    },
                },
                "required": ["chapter_number", "scene_index"],
                "additionalProperties": False,
            },
        }

    async def execute(
        self, chapter_number: int, scene_index: int, **kwargs
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = SceneShowrunnerCheckpointDB(pid)
            ok = await db.clear_checkpoint(chapter_number, scene_index)
            return json.dumps(
                {
                    "cleared": ok,
                    "chapter_number": chapter_number,
                    "scene_index": scene_index,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("clear_checkpoint 失败")
            return json.dumps(
                {"error": f"清理 checkpoint 失败: {e}"},
                ensure_ascii=False,
            )
