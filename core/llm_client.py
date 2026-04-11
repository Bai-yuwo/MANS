"""
MANS LLM 客户端模块

对接豆包（Doubao）大模型的极简异步客户端，
基于 OpenAI 兼容接口实现流式生成。
"""

import os
from openai import AsyncOpenAI


class DoubaoClient:
    """
    豆包大模型异步客户端
    
    封装与豆包 API 的交互逻辑，支持流式输出。
    配置通过环境变量读取，确保敏感信息不硬编码。
    """
    
    def __init__(self) -> None:
        """
        初始化客户端
        
        从环境变量读取 API 凭证和端点地址。
        
        Raises:
            ValueError: 当 DOUBAO_API_KEY 未配置时抛出
        """
        # 获取 API 密钥，必填
        api_key = os.getenv("DOUBAO_API_KEY")
        if not api_key:
            raise ValueError("环境变量 DOUBAO_API_KEY 未配置，请先设置豆包 API 密钥")
        
        # 获取 API 端点，选填，有默认值
        api_base = os.getenv("DOUBAO_API_BASE", "https://ark.cn-beijing.volces.com/api/v3")
        
        # 初始化 OpenAI 兼容客户端
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
        
        通过异步流式接口逐token返回，并在每个token生成时
        通过事件总线实时推送，供 SSE 等场景使用。
        
        Args:
            messages: 对话消息列表，格式为 OpenAI Chat Completion 格式
            model: 模型 ID（豆包接入点 ID，通常以 ep- 开头）
            event_bus: 事件总线实例，用于发布 LLM_STREAM_TOKEN 事件
        
        Returns:
            str: 拼接后的完整响应文本
        """
        # 调用流式接口
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True  # 开启流式输出
        )
        
        # 用于拼接完整响应
        full_content = ""
        
        # 异步遍历流式响应
        async for chunk in response:
            # 提取 delta 内容
            delta = chunk.choices[0].delta.content
            
            # 只要 token 不为空就立即发布事件
            if delta:
                full_content += delta
                await event_bus.publish(
                    "LLM_STREAM_TOKEN",
                    {"token": delta}
                )
        
        return full_content
