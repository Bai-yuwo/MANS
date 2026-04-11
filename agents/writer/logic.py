"""
MANS 写作智能体模块 (解耦版)

严格贯彻模块化设计：
- 读取 config.yml 获取配置属性
- 读取 prompt.j2 渲染提示词模板
- 执行无状态的生成逻辑并返回结果
"""

import os
import json
import yaml
from jinja2 import Environment, FileSystemLoader

from core.llm_client import DoubaoClient
from core.event_bus import EventType
from core.state_manager import state_manager


class WriterAgent:
    """
    解耦后的写作智能体
    """

    def __init__(self):
        """
        初始化时加载解耦的配置文件和模板文件
        """
        # 获取当前文件所在目录 (mans_system/agents/writer)
        self.agent_dir = os.path.dirname(os.path.abspath(__file__))

        # 1. 加载 YAML 配置
        config_path = os.path.join(self.agent_dir, "config.yml")
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        # 2. 初始化 Jinja2 模板环境
        self.jinja_env = Environment(loader=FileSystemLoader(self.agent_dir))
        self.template = self.jinja_env.get_template("prompt.j2")

    async def run(self, payload: dict, event_bus) -> None:
        try:
            # 发布启动事件，使用从 config.yml 中读取的 name
            await event_bus.publish(
                EventType.AGENT_START,
                {"agent": self.config.get("name", "WriterAgent"), "status": "running"}
            )

            # 获取模型 ID
            model_id = os.getenv("DOUBAO_MODEL_WRITING")
            if not model_id:
                raise ValueError("环境变量 DOUBAO_MODEL_WRITING 未配置。")

            # 从外部读取物理记忆 (无状态机制的核心)
            project_name = payload.get("project_name", "default_project")
            context = await state_manager.get_project_context(project_name)

            # 将字典转为格式化的 JSON 字符串
            world_setting_str = json.dumps(context.get("world_setting", {}), ensure_ascii=False, indent=2)
            characters_str = json.dumps(context.get("characters", {}), ensure_ascii=False, indent=2)
            plot = payload.get("plot", "默认情节：主角在雨中醒来。")

            # 渲染 Jinja2 模板
            system_prompt = self.template.render(
                world_setting=world_setting_str,
                characters=characters_str,
                plot=plot
            )

            messages = [
                {"role": "system", "content": system_prompt}
            ]

            # 发布提示词构建完成事件 (白盒监控)
            await event_bus.publish(
                EventType.PROMPT_BUILT,
                {"messages": messages}
            )

            # 调用大模型
            client = DoubaoClient()
            full_content = await client.stream_generate(
                messages=messages,
                model=model_id,
                event_bus=event_bus
            )

            # 保存草稿
            chapter_title = payload.get("chapter_title", "未命名章节")
            saved_path = await state_manager.save_draft(project_name, chapter_title, full_content)

            await event_bus.publish(
                EventType.SYSTEM_INFO,
                {"message": f"章节已保存至: {saved_path}"}
            )

            # 发布结束事件
            await event_bus.publish(
                EventType.LLM_END,
                {"agent": self.config.get("name", "WriterAgent"), "status": "completed"}
            )

        except Exception as e:
            await event_bus.publish(
                EventType.ERROR,
                {"error": str(e), "agent": getattr(self, 'config', {}).get("name", "WriterAgent")}
            )