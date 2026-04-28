"""
tools/system/confirm_stage_advance.py

阶段推进确认工具 — Director 专属,触发前端用户确认弹窗。

设计:
    Director 在阶段切换前(INIT→PLAN / PLAN→WRITE / WRITE 跨章)调用本工具,
    本工具通过 sink 向外层(Orchestrator)推送一个 type="confirm" 的 StreamPacket,
    然后返回正常 JSON 字符串(作为 function_call_output 给 Director LLM 的下一轮)。

    Director LLM 的 system prompt 里已约定:"调用 confirm_stage_advance 后
    本轮不要调用任何其他工具,简要总结并结束。"

    Orchestrator 收到 confirm 包后暂停 Director,等用户回复,再续接 previous_response_id。
"""

import json
from typing import Awaitable, Callable, ClassVar, Optional

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.project_config import get_project_config
from core.stream_packet import ConfirmPayload, StreamPacket

logger = get_logger("tools.system.confirm_stage_advance")

PacketSink = Callable[[StreamPacket], Awaitable[None]]


class ConfirmStageAdvance(BaseTool):
    """Director 请求用户确认阶段切换,并向 Orchestrator 推送 confirm packet。"""

    @property
    def name(self) -> str:
        return "confirm_stage_advance"

    @property
    def description(self) -> str:
        return (
            "向用户发出阶段切换确认请求。Director 在完成一个阶段后,调用本工具暂停工作流,"
            "等用户确认后再进入下一阶段。本工具会触发前端弹窗,用户必须回复'同意'或给出修改意见。"
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
                    "from_stage": {
                        "type": "string",
                        "description": "当前阶段名,如 'INIT' / 'PLAN' / 'WRITE'",
                    },
                    "to_stage": {
                        "type": "string",
                        "description": "待进入的阶段名,如 'PLAN' / 'WRITE'",
                    },
                    "summary": {
                        "type": "string",
                        "description": "当前阶段已完成的成果摘要(供用户了解发生了什么)",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "给用户的确认问句,如'是否确认进入 PLAN 阶段?直接回复\"同意\"或提出修改意见'",
                    },
                },
                "required": ["from_stage", "to_stage", "summary", "prompt"],
                "additionalProperties": False,
            },
        }

    def __init__(self):
        self._packet_sink: Optional[PacketSink] = None
        self._pending_payload: Optional[ConfirmPayload] = None

    def with_stream_sink(self, sink: Optional[PacketSink]) -> "ConfirmStageAdvance":
        """设置包 sink(Director 的 _dispatch_tools 调用前注入)。返回 self 便于链式。"""
        self._packet_sink = sink
        return self

    async def execute(
        self,
        *,
        from_stage: str,
        to_stage: str,
        summary: str,
        prompt: str,
        **kwargs,
    ) -> str:
        # 防御性校验：防止 LLM 传错 to_stage（如 INIT→INIT）
        if from_stage == to_stage:
            valid_next = {
                "INIT": "PLAN",
                "PLAN": "WRITE",
                "WRITE": "WRITE",
            }
            corrected = valid_next.get(from_stage, to_stage)
            if corrected != to_stage:
                logger.warning(
                    f"Director 传了错误的 to_stage='{to_stage}'，"
                    f"自动修正为 '{corrected}' (from_stage='{from_stage}')"
                )
                to_stage = corrected

        # --- 自动确认检查 ---
        try:
            pid = require_current_project_id()
            config = await get_project_config(pid)
            if config.get("auto_advance", False):
                logger.info(
                    f"[auto_advance] 自动批准阶段切换: {from_stage} → {to_stage}"
                )
                return json.dumps(
                    {
                        "status": "auto_approved",
                        "message": f"auto_advance=true，自动批准 {from_stage} → {to_stage}",
                        "from_stage": from_stage,
                        "to_stage": to_stage,
                    },
                    ensure_ascii=False,
                )
        except Exception:
            pass

        payload = ConfirmPayload(
            from_stage=from_stage,
            to_stage=to_stage,
            summary=summary,
            prompt=prompt,
        )
        self._pending_payload = payload

        # 通过 sink 推送 confirm packet 到 Director 的 yield 流
        if self._packet_sink is not None:
            try:
                await self._packet_sink(StreamPacket(type="confirm", content=payload))
            except Exception as e:
                logger.warning(f"confirm packet sink 推送失败: {e}")

        return json.dumps(
            {
                "status": "confirmation_emitted",
                "message": "已向 Orchestrator 发出确认请求,等待用户回复后继续。",
                "from_stage": from_stage,
                "to_stage": to_stage,
            },
            ensure_ascii=False,
        )

    def consume_pending(self) -> Optional[ConfirmPayload]:
        """取出并清除 pending confirm payload(供 Orchestrator 查询)。"""
        p = self._pending_payload
        self._pending_payload = None
        return p
