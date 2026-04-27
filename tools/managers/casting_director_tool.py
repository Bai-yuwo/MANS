"""
tools/managers/casting_director_tool.py

Director 调用 CastingDirector 的 ManagerTool 包装。
"""

from core.manager_tool import ManagerTool
from agents.managers.casting_director import CastingDirector


class CallCastingDirector(ManagerTool):
    target_manager_class = CastingDirector
    description = (
        "调用 CastingDirector 主管(INIT 阶段),完成角色塑造与关系网设计。"
        "输入 user_prompt 描述角色需求,CastingDirector 将自主调用 PortraitDesigner / RelationDesigner "
        "专家并落盘 characters/* 与 relationships.json。返回 turns / tokens / summary。"
    )
