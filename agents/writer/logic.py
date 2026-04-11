"""
MANS 写作智能体模块

核心写作智能体实现，基于事件驱动架构，
通过调用大模型进行小说内容生成。
"""

import os
from core.llm_client import DoubaoClient


class WriterAgent:
    """
    写作智能体
    
    负责接收情节设定，调用大模型生成小说正文。
    全程通过事件总线发布状态，便于前端实时展示。
    """
    
    async def run(self, payload: dict, event_bus) -> None:
        """
        执行写作任务
        
        完整的执行流程：启动 → 构建提示词 → 调用模型 → 结束。
        任何阶段发生异常都会被捕获并发布 ERROR 事件。
        
        Args:
            payload: 任务参数字典，包含情节设定等
            event_bus: 事件总线实例
        """
        try:
            # ========== 步骤 1：发布启动事件 ==========
            await event_bus.publish(
                "AGENT_START",
                {"agent": "WriterAgent", "status": "running"}
            )
            
            # ========== 步骤 2：获取模型 ID ==========
            model_id = os.getenv("DOUBAO_MODEL_WRITING")
            if not model_id:
                raise ValueError(
                    "环境变量 DOUBAO_MODEL_WRITING 未配置。"
                    "请设置豆包写作模型的接入点 ID（ep- 开头），"
                    "可在火山引擎控制台获取。"
                )
            
            # ========== 步骤 3：构建提示词 ==========
            messages = [
                {
                    "role": "system",
                    "content": "你是一位网文大神。请根据用户提供的情节，用生动的描写写出一段小说正文。"
                },
                {
                    "role": "user",
                    "content": payload.get("plot", "男主在雨夜中醒来，失去了记忆。")
                }
            ]
            
            # ========== 步骤 4：发布提示词构建完成事件 ==========
            await event_bus.publish(
                "PROMPT_BUILT",
                {"messages": messages}  # 白盒监控用，暴露完整消息
            )
            
            # ========== 步骤 5：调用大模型流式生成 ==========
            # 实例化客户端并执行流式生成
            client = DoubaoClient()
            await client.stream_generate(
                messages=messages,
                model=model_id,
                event_bus=event_bus
            )
            
            # ========== 步骤 6：发布结束事件 ==========
            await event_bus.publish(
                "AGENT_END",
                {"agent": "WriterAgent", "status": "completed"}
            )
            
        except Exception as e:
            # 统一异常捕获，发布错误事件
            await event_bus.publish(
                "ERROR",
                {"error": str(e), "agent": "WriterAgent"}
            )
