"""
tools/managers/plot_architect_tool.py

Director 调用 PlotArchitect 的 ManagerTool 包装。
"""

from core.manager_tool import ManagerTool
from agents.managers.plot_architect import PlotArchitect


class CallPlotArchitect(ManagerTool):
    target_manager_class = PlotArchitect
    description = (
        "调用 PlotArchitect 主管(PLAN 阶段),完成大纲/故事弧/章节场景设计。"
        "输入 user_prompt 描述故事方向,PlotArchitect 将自主调用 ArcDesigner / ChapterDesigner "
        "专家并落盘 outline.json / arcs/* / chapters/*_plan.json。返回 turns / tokens / summary。"
    )
