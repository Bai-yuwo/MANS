"""
MANS LLM 客户端模块

对接豆包（Doubao）大模型的极简异步客户端，
基于 OpenAI 兼容接口实现流式生成。
"""

import os
from openai import AsyncOpenAI
# 【Bug修复】引入 EventType 枚举
from core.event_bus import EventType


class DoubaoClient:
    """
    豆包大模型异步客户端
    """

    def __init__(self) -> None:
        """
        初始化客户端
        """
        api_key = os.getenv("DOUBAO_API_KEY")
        if not api_key:
            raise ValueError("环境变量 DOUBAO_API_KEY 未配置，请先设置豆包 API 密钥")

        api_base = os.getenv("DOUBAO_API_BASE", "https://ark.cn-beijing.volces.com/api/v3")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base
        )

    async def stream_generate(
        self,
        messages: list,
        model: str,
        event_bus
    ) -> str:
        """
        流式调用大模型生成文本
        """
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True
        )

        full_content = ""

        async for chunk in response:
            delta = chunk.choices[0].delta.content

            if delta:
                full_content += delta
                # 【Bug修复】使用 EventType 枚举而不是字符串
                await event_bus.publish(
                    EventType.LLM_STREAM_TOKEN,
                    {"token": delta}
                )

        return full_content