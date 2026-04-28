"""
tools/system/ask_user.py

AGENT 通用用户询问工具 — 当遇到模糊、矛盾或不确定的信息时暂停生成，向用户提问。

设计:
    与 confirm_stage_advance 共享同一套 pause/resume 机制：
    1. 通过 sink 向外层(Orchestrator)推送 type="ask_user" 的 StreamPacket
    2. 返回正常 JSON 字符串作为 function_call_output
    3. BaseAgent 检测到 ask_user 后暂停 ReAct 循环
    4. 用户回复后 Orchestrator 通过 /respond 续接，Director 继续执行

    数据复用 ConfirmPayload，通过 kind="user_question" 区分于阶段确认。

使用场景:
    - 用户需求存在矛盾、模糊或缺失关键信息
    - 需要在多个方案中让用户做选择
    - 生成内容涉及敏感主题，需要用户明确许可
    - 任何 AGENT 不确定是否应该继续的情况
"""

import json
from typing import Awaitable, Callable, ClassVar, List, Optional

from core.base_tool import BaseTool
from core.context import require_current_project_id
from core.logging_config import get_logger
from core.project_config import get_project_config
from core.stream_packet import ConfirmPayload, StreamPacket

logger = get_logger("tools.system.ask_user")

PacketSink = Callable[[StreamPacket], Awaitable[None]]


class AskUser(BaseTool):
    """AGENT 向用户发起通用询问，暂停生成等待答复。"""

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "当你遇到模糊、矛盾、缺失关键信息或不确定的情况时，调用本工具向用户询问。"
            "本工具会暂停当前工作流，弹出询问窗口，等用户回复后继续。"
            "调用后本轮不要再调用其他工具。"
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
                    "question": {
                        "type": "string",
                        "description": "具体问题文本。应简洁明确，直接提出你需要用户回答的问题。",
                    },
                    "context": {
                        "type": "string",
                        "description": "问题上下文/背景说明。解释为什么问这个问题，当前遇到了什么情况。",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选项列表。如果用户可以从几个预设选项中选择，提供此字段；若为开放性问题则留空。",
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        }

    def __init__(self):
        self._packet_sink: Optional[PacketSink] = None
        self._pending_payload: Optional[ConfirmPayload] = None

    def with_stream_sink(self, sink: Optional[PacketSink]) -> "AskUser":
        """设置包 sink(BaseAgent 的 _dispatch_tools 调用前注入)。返回 self 便于链式。"""
        self._packet_sink = sink
        return self

    async def execute(
        self,
        *,
        question: str,
        context: str = "",
        options: Optional[List[str]] = None,
        **kwargs,
    ) -> str:
        # --- 自动重写检查 ---
        try:
            pid = require_current_project_id()
            cfg = await get_project_config(pid)
            if cfg.get("auto_rewrite", False) and "重写" in question:
                logger.info(f"[auto_rewrite] 自动接受重写建议: {question[:60]}...")
                return json.dumps(
                    {
                        "status": "auto_replied",
                        "reply": "接受重写",
                        "message": "auto_rewrite=true，自动接受重写建议",
                        "question": question,
                    },
                    ensure_ascii=False,
                )
        except Exception:
            pass

        payload = ConfirmPayload(
            kind="user_question",
            question=question,
            context=context,
            options=options or [],
        )
        self._pending_payload = payload

        # 通过 sink 推送 ask_user packet 到 Director 的 yield 流
        if self._packet_sink is not None:
            try:
                await self._packet_sink(StreamPacket(type="ask_user", content=payload))
            except Exception as e:
                logger.warning(f"ask_user packet sink 推送失败: {e}")

        return json.dumps(
            {
                "status": "question_emitted",
                "message": "已向用户发出询问请求，等待用户回复后继续。",
                "question": question,
                "context": context,
                "options": options or [],
            },
            ensure_ascii=False,
        )

    def consume_pending(self) -> Optional[ConfirmPayload]:
        """取出并清除 pending ask_user payload(供 Orchestrator 查询)。"""
        p = self._pending_payload
        self._pending_payload = None
        return p
