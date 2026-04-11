"""
MANS 状态管理器模块 (StateManager)

负责无状态智能体与本地文件系统（Workspace）之间的“长期记忆”交互。
所有文件 I/O 均采用 aiofiles 进行异步非阻塞操作，确保高并发下的性能。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

import aiofiles


class StateManager:
    """
    工作区状态管理器

    统一管理各个独立小说项目的数据，包括设定库（记忆）和正文草稿。
    """

    def __init__(self, workspace_root: str = "workspace") -> None:
        """
        初始化状态管理器

        Args:
            workspace_root: 工作区根目录路径，默认为当前目录下的 workspace
        """
        self.workspace_root = Path(workspace_root)
        # 确保根目录存在
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    async def get_project_context(self, project_name: str) -> Dict[str, Any]:
        """
        获取指定项目的上下文记忆

        尝试读取项目的世界观 (world.json) 和人物设定 (characters.json)。
        如果文件不存在，则返回空字典，不会抛出异常。

        Args:
            project_name: 项目文件夹名称

        Returns:
            Dict: 包含 'world_setting' 和 'characters' 的字典
        """
        project_path = self.workspace_root / project_name
        memory_path = project_path / "memory"

        context = {
            "world_setting": {},
            "characters": {}
        }

        # 如果 memory 文件夹不存在，直接返回空上下文
        if not memory_path.exists():
            return context

        # 异步读取世界观设定
        world_file = memory_path / "world.json"
        if world_file.exists():
            try:
                async with aiofiles.open(world_file, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                    context["world_setting"] = json.loads(content)
            except Exception as e:
                # 在实际工程中，这里可以接入 EventBus 抛出警告事件
                print(f"[StateManager Warning] 读取 world.json 失败: {e}")

        # 异步读取人物设定
        char_file = memory_path / "characters.json"
        if char_file.exists():
            try:
                async with aiofiles.open(char_file, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                    context["characters"] = json.loads(content)
            except Exception as e:
                print(f"[StateManager Warning] 读取 characters.json 失败: {e}")

        return context

    async def save_draft(self, project_name: str, chapter_title: str, content: str) -> str:
        """
        保存小说正文草稿

        将 LLM 生成的正文异步写入到 Markdown 文件中。

        Args:
            project_name: 项目文件夹名称
            chapter_title: 章节标题（将作为文件名）
            content: 正文内容

        Returns:
            str: 保存的绝对路径
        """
        project_path = self.workspace_root / project_name
        drafts_path = project_path / "drafts"

        # 确保草稿文件夹存在
        drafts_path.mkdir(parents=True, exist_ok=True)

        # 清理标题中可能导致文件系统错误的非法字符
        safe_title = "".join(c for c in chapter_title if c.isalnum() or c in (' ', '-', '_', '第', '章')).rstrip()
        if not safe_title:
            safe_title = "未命名章节"

        file_path = drafts_path / f"{safe_title}.md"

        # 异步写入文件
        async with aiofiles.open(file_path, mode='w', encoding='utf-8') as f:
            await f.write(content)

        return str(file_path.absolute())

    async def update_memory(self, project_name: str, memory_type: str, data: Dict[str, Any]) -> None:
        """
        更新项目的记忆库

        供后续的“伏笔智能体/大纲智能体”调用，覆盖写入结构化数据。

        Args:
            project_name: 项目文件夹名称
            memory_type: 记忆类型（如 'world', 'characters', 'outline'），作为文件名
            data: 需要保存的字典数据
        """
        project_path = self.workspace_root / project_name
        memory_path = project_path / "memory"

        # 确保记忆文件夹存在
        memory_path.mkdir(parents=True, exist_ok=True)

        file_path = memory_path / f"{memory_type}.json"

        # 异步格式化写入 JSON（包含缩进，方便人类阅读和干预）
        async with aiofiles.open(file_path, mode='w', encoding='utf-8') as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=4))


# 实例化全局单例（类似 event_bus 的用法）
state_manager = StateManager()