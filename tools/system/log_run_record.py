"""
tools/system/log_run_record.py

落主管/专家执行 trace 到 `workspace/{pid}/runs/{run_id}.json`。

由 Director / Orchestrator / 各主管在结束一段调用后写入。AgentRunRecord 字段
对齐 schemas.AgentRunRecord(开始/结束时间、token 用量、tool 调用计数等)。
"""

import json

from knowledge_bases.base_db import BaseDB

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger

logger = get_logger("tools.system.log_run_record")


class LogRunRecord(BaseTool):
    @property
    def name(self) -> str:
        return "log_run_record"

    @property
    def description(self) -> str:
        return (
            "落一条 Agent 运行记录到 runs/。record 字段对齐 schemas.AgentRunRecord;"
            "若已存在同 run_id 文件则深度合并(便于分批写入开始/结束)。"
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
                    "run_id": {"type": "string"},
                    "record": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
                "required": ["run_id", "record"],
                "additionalProperties": False,
            },
        }

    async def execute(self, run_id: str, record: dict, **kwargs) -> str:
        if not run_id:
            return json.dumps({"error": "run_id 不能为空"}, ensure_ascii=False)
        if not isinstance(record, dict):
            return json.dumps({"error": "record 必须是对象"}, ensure_ascii=False)

        try:
            pid = require_current_project_id()
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        try:
            db = BaseDB(pid, "runs")
            ok = await db.save(run_id, record)
            return json.dumps({"saved": ok, "run_id": run_id}, ensure_ascii=False)
        except Exception as e:
            logger.exception("log_run_record 失败")
            return json.dumps({"error": f"写入失败: {e}"}, ensure_ascii=False)
