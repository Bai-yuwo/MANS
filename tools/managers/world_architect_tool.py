"""
tools/managers/world_architect_tool.py

Director 调用 WorldArchitect 的 ManagerTool 包装。
"""

from core.manager_tool import ManagerTool
from agents.managers.world_architect import WorldArchitect


class CallWorldArchitect(ManagerTool):
    target_manager_class = WorldArchitect
    description = (
        "调用 WorldArchitect 主管(INIT 阶段),完成世界观/地理/规则设计。"
        "输入 user_prompt 描述世界观需求,WorldArchitect 将自主调用 Geographer / RuleSmith "
        "专家并落盘 bible.json 与 foreshadowing.json。返回 turns / tokens / summary。"
    )
