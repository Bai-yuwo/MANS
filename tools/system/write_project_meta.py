"""
tools/system/write_project_meta.py

写入项目元信息 — Director 专属,用于更新当前项目 stage/status/current_chapter 等。

设计:
    Director 在阶段切换后(用户确认后)调用本工具,把 project_meta.json 中的 stage
    更新为新阶段。本工具不是 KB 共享读,也不是业务主管写,而是 Director 级别的元信息
    状态切换。

    采用 JSON merge(增量更新),不覆盖其他字段。
"""

import json
from pathlib import Path

import aiofiles

from core.base_tool import BaseTool
from core.config import get_config
from core.context import require_current_project_id
from core.logging_config import get_logger

logger = get_logger("tools.system.write_project_meta")


class WriteProjectMeta(BaseTool):
    """写入项目元数据(stage / status / current_chapter 等)。"""

    @property
    def name(self) -> str:
        return "write_project_meta"

    @property
    def description(self) -> str:
        return (
            "更新当前项目的元信息。用于阶段切换(如 INIT->PLAN)时写入新的 stage,"
            "或更新 current_chapter 等状态字段。采用增量更新,不覆盖其他已有字段。"
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
                    "stage": {
                        "type": "string",
                        "description": "项目当前阶段,如 'INIT' / 'PLAN' / 'WRITE' / 'COMPLETED'",
                    },
                    "status": {
                        "type": "string",
                        "description": "项目当前状态,如 'active' / 'paused' / 'completed'",
                    },
                    "current_chapter": {
                        "type": "integer",
                        "description": "当前正在写的章节号(WRITE 阶段使用)",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    async def execute(
        self,
        *,
        stage: str | None = None,
        status: str | None = None,
        current_chapter: int | None = None,
        **kwargs,
    ) -> str:
        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        cfg = get_config()
        meta_path = Path(cfg.WORKSPACE_PATH) / pid / "project_meta.json"

        # 读取现有
        current: dict = {}
        if meta_path.exists():
            try:
                async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                current = json.loads(content)
            except Exception as e:
                logger.warning(f"读取现有 project_meta 失败,将新建: {e}")

        # 增量更新
        if stage is not None:
            current["stage"] = stage
        if status is not None:
            current["status"] = status
        if current_chapter is not None:
            current["current_chapter"] = current_chapter

        # 原子写入
        tmp_path = meta_path.with_suffix(".tmp")
        try:
            async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(current, ensure_ascii=False, indent=2))
            tmp_path.replace(meta_path)
        except Exception as e:
            logger.exception("写入 project_meta 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)

        logger.info(f"project_meta 已更新: stage={stage}, status={status}")
        return json.dumps(
            {"status": "ok", "updated_fields": [k for k, v in [("stage", stage), ("status", status), ("current_chapter", current_chapter)] if v is not None]},
            ensure_ascii=False,
        )
