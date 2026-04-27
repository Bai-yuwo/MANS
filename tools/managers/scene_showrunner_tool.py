"""
tools/managers/scene_showrunner_tool.py

Director 调用 SceneShowrunner 的 ManagerTool 包装。

注意:SceneShowrunner 的 user_prompt 通常需要携带 chapter_number / scene_index 等上下文。
Director 应该在 user_prompt 中把这些信息写清楚。
"""

from core.manager_tool import ManagerTool
from agents.managers.scene_showrunner import SceneShowrunner


class CallSceneShowrunner(ManagerTool):
    target_manager_class = SceneShowrunner
    description = (
        "调用 SceneShowrunner 主管(WRITE 阶段),驱动单场景流水线。"
        "输入 user_prompt 需携带 chapter_number 与 scene_index,SceneShowrunner 将编排 "
        "SceneDirector→Writer(流式)→Critic+Continuity(并行)→ReviewManager→Scribe 完整闭环,"
        "落盘 scene_beatsheets/* / chapters/*_draft.json / review/* / apply_kb_diff。"
        "返回 turns / tokens / summary。"
    )
