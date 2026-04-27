"""
knowledge_bases/checkpoint_db.py

SceneShowrunner 断点续接的 checkpoint 持久化层。

设计动机:
    WRITE 阶段单场景流水线(SceneShowrunner)包含多个步骤:
    beatsheet → draft → review → guidance → (rewrite) → final → kb_diff
    若进程崩溃或服务器重启,previous_response_id 丢失,ReAct 循环无法续接。
    Checkpoint 记录每个场景已完成的步骤,使 SceneShowrunner 重启后能从断点继续。

存储结构:
    workspace/{project_id}/checkpoints/scene_showrunner.json
    {
        "checkpoints": {
            "{chapter_number}:{scene_index}": {
                "beatsheet": {"completed_at": "..."},
                "draft": {"completed_at": "...", "rewrite_attempt": 0},
                "review_issues": {"completed_at": "..."},
                "rewrite_guidance": {"completed_at": "...", "rewrite_attempt": 0},
                "final": {"completed_at": "..."},
                "kb_diff": {"completed_at": "..."},
                "started_at": "...",
                "last_updated": "..."
            }
        }
    }

集成方式:
    - 写工具(save_scene_beatsheet / save_scene_draft 等)在成功落盘后自动写入 checkpoint
    - SceneShowrunner 启动时调用 read_checkpoint 工具,根据已完成的步骤跳过
    - 场景全部完成后(或用户显式要求)调用 clear_checkpoint 清理
"""

import time
from typing import Optional

from knowledge_bases.base_db import BaseDB
from core.logging_config import get_logger

logger = get_logger("knowledge_bases.checkpoint_db")


class SceneShowrunnerCheckpointDB:
    """
    SceneShowrunner 断点续接 checkpoint 管理器。

    每个 checkpoint 按 "chapter_number:scene_index" 键存储,
    记录该场景流水线各步骤的完成状态。
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._db = BaseDB(project_id, "checkpoints")

    async def save_checkpoint(
        self,
        chapter_number: int,
        scene_index: int,
        step: str,
        extra: Optional[dict] = None,
    ) -> bool:
        """
        记录某个步骤已完成。

        Args:
            chapter_number: 章节号
            scene_index: 场景索引
            step: 步骤名(beatsheet/draft/review_issues/rewrite_guidance/final/kb_diff)
            extra: 额外数据(如 draft 的 rewrite_attempt)

        Returns:
            是否保存成功
        """
        data = await self._db.load("scene_showrunner") or {"checkpoints": {}}
        key = f"{chapter_number}:{scene_index}"

        if key not in data["checkpoints"]:
            data["checkpoints"][key] = {
                "started_at": time.time(),
                "steps": {},
            }

        checkpoint = data["checkpoints"][key]
        checkpoint["steps"][step] = {
            "completed_at": time.time(),
            **(extra or {}),
        }
        checkpoint["last_updated"] = time.time()

        ok = await self._db.save("scene_showrunner", data)
        if ok:
            logger.info(f"Checkpoint 已记录: {key}/{step}")
        return ok

    async def get_checkpoint(
        self, chapter_number: int, scene_index: int
    ) -> dict:
        """
        读取指定场景的 checkpoint。

        Args:
            chapter_number: 章节号
            scene_index: 场景索引

        Returns:
            checkpoint 字典。若无记录返回 {"steps": {}}。
        """
        data = await self._db.load("scene_showrunner")
        if not data or "checkpoints" not in data:
            return {"steps": {}}

        key = f"{chapter_number}:{scene_index}"
        cp = data["checkpoints"].get(key, {})
        return cp if cp else {"steps": {}}

    async def get_completed_steps(
        self, chapter_number: int, scene_index: int
    ) -> list[str]:
        """
        获取已完成步骤的列表(按写入顺序)。

        Returns:
            已完成步骤名列表
        """
        cp = await self.get_checkpoint(chapter_number, scene_index)
        return list(cp.get("steps", {}).keys())

    async def clear_checkpoint(
        self, chapter_number: int, scene_index: int
    ) -> bool:
        """
        清理指定场景的 checkpoint(通常在场景全部完成后调用)。

        Args:
            chapter_number: 章节号
            scene_index: 场景索引

        Returns:
            是否清理成功。无记录时返回 True(幂等)。
        """
        data = await self._db.load("scene_showrunner")
        if not data or "checkpoints" not in data:
            return True

        key = f"{chapter_number}:{scene_index}"
        if key in data["checkpoints"]:
            del data["checkpoints"][key]
            ok = await self._db.save("scene_showrunner", data)
            if ok:
                logger.info(f"Checkpoint 已清理: {key}")
            return ok
        return True

    async def is_step_completed(
        self, chapter_number: int, scene_index: int, step: str
    ) -> bool:
        """检查某步骤是否已完成。"""
        cp = await self.get_checkpoint(chapter_number, scene_index)
        return step in cp.get("steps", {})
