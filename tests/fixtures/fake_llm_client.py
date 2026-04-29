"""
fake_llm_client.py — 测试用 Fake LLM 客户端

模拟 core.llm_client.LLMClient 的两个核心接口:
  - call(): 专家一次性同步调用 → LLMResponse
  - stream_call(): 主管 ReAct 流式调用 → AsyncIterator[StreamPacket]

用法:
    fake_llm = FakeLLMClient()
    fake_llm.enqueue(LLMResponse(content='{"name":"测试角色"}', model='test'))
    fake_llm.enqueue_react_sequence([
        {"tool_calls": [{"name": "read_bible", "arguments": {}}]},
        {"content": "任务完成"},
    ])
"""

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from core.llm_client import LLMResponse
from core.stream_packet import CompletedPayload, StreamPacket


@dataclass
class FakeLLMCallLog:
    """记录一次 LLM 调用的参数快照。"""
    method: str
    agent_name: str
    args: dict = field(default_factory=dict)


class FakeLLMClient:
    """
    可编排响应序列的伪 LLM 客户端。

    特性:
      - 线程安全(单测单线程,queue 简单 list 即可)
      - 支持 call() 和 stream_call() 两种模式
      - 支持工具调用序列编排
    """

    def __init__(self):
        self._call_queue: list[LLMResponse | list[StreamPacket]] = []
        self.call_log: list[FakeLLMCallLog] = []

    # --------------------------------------------------------
    # 编排接口
    # --------------------------------------------------------

    def enqueue(self, response: LLMResponse) -> "FakeLLMClient":
        """排队一个 call() 响应。"""
        self._call_queue.append(response)
        return self

    def enqueue_stream(self, packets: list[StreamPacket]) -> "FakeLLMClient":
        """排队一组 stream_call() 包。"""
        self._call_queue.append(packets)
        return self

    def enqueue_react_sequence(
        self, turns: list[dict]
    ) -> "FakeLLMClient":
        """
        快速编排 ReAct 序列。

        每轮 dict 可选键:
          - tool_calls: list[{"name": str, "arguments": dict}]
          - content: str (最终文本)
          - res_id: str
          - total_tokens: int
        """
        for t in turns:
            tool_calls = t.get("tool_calls")
            content = t.get("content", "")
            res_id = t.get("res_id", "fake_res_001")
            total_tokens = t.get("total_tokens", 100)

            packets: list[StreamPacket] = []
            if content:
                packets.append(
                    StreamPacket(
                        type="output",
                        content=content,
                        agent_name="fake_agent",
                    )
                )
            packets.append(
                StreamPacket(
                    type="completed",
                    content=CompletedPayload(
                        res_id=res_id,
                        total_tokens=total_tokens,
                        tool_calls=tool_calls or [],
                    ),
                    agent_name="fake_agent",
                )
            )
            self._call_queue.append(packets)
        return self

    def reset(self) -> "FakeLLMClient":
        """清空队列和日志。"""
        self._call_queue.clear()
        self.call_log.clear()
        return self

    # --------------------------------------------------------
    # 模拟 LLMClient 接口
    # --------------------------------------------------------

    async def call(
        self,
        agent_name: str,
        system_prompt: str = "",
        user_prompt: str = "",
        json_schema: Optional[dict] = None,
        **kwargs,
    ) -> LLMResponse:
        """模拟专家一次性调用。"""
        self.call_log.append(
            FakeLLMCallLog(
                method="call",
                agent_name=agent_name,
                args={
                    "system_prompt": system_prompt[:200],
                    "user_prompt": user_prompt[:200],
                    "json_schema": json_schema,
                },
            )
        )
        if not self._call_queue:
            raise RuntimeError(
                f"FakeLLMClient.call() 队列已空,agent={agent_name}"
            )
        resp = self._call_queue.pop(0)
        if isinstance(resp, LLMResponse):
            return resp
        # 若排队的是 StreamPacket 列表,取最后一个 completed 包的内容当文本
        text_parts = []
        for p in resp:
            if p.type == "output":
                text_parts.append(str(p.content))
        return LLMResponse(
            content="".join(text_parts) or "{}",
            model="fake-model",
        )

    async def stream_call(
        self,
        agent_name: str,
        input_data: list[dict],
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
        previous_response_id: Optional[str] = None,
        thinking: Optional[dict] = None,
        enable_caching: bool = False,
        expire_at: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[StreamPacket]:
        """模拟主管流式调用。"""
        self.call_log.append(
            FakeLLMCallLog(
                method="stream_call",
                agent_name=agent_name,
                args={
                    "input_data": input_data,
                    "tools_count": len(tools) if tools else 0,
                    "previous_response_id": previous_response_id,
                },
            )
        )
        if not self._call_queue:
            raise RuntimeError(
                f"FakeLLMClient.stream_call() 队列已空,agent={agent_name}"
            )
        resp = self._call_queue.pop(0)
        if isinstance(resp, LLMResponse):
            # call 响应包装为单包流
            yield StreamPacket(
                type="output",
                content=resp.content,
                agent_name=agent_name,
            )
            yield StreamPacket(
                type="completed",
                content=CompletedPayload(
                    res_id=resp.res_id or "fake_res",
                    total_tokens=resp.usage.get("total_tokens", 100)
                    if resp.usage
                    else 100,
                    tool_calls=[],
                ),
                agent_name=agent_name,
            )
        else:
            for p in resp:
                yield p
