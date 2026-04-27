"""
tools/managers/

ManagerTool 子类 — 把 4 个业务主管(WorldArchitect / PlotArchitect / CastingDirector /
SceneShowrunner)包装成 Director 可调用的工具。

每个工具都是 ManagerTool 子类,自动注册到 ToolManager:
    call_world_architect     → 触发 WorldArchitect ReAct 循环
    call_plot_architect      → 触发 PlotArchitect ReAct 循环
    call_casting_director    → 触发 CastingDirector ReAct 循环
    call_scene_showrunner    → 触发 SceneShowrunner ReAct 循环

自动发现路径:
    tools/__init__.py → from . import managers → 触发本文件 import → ToolManager 扫描注册
"""

from .world_architect_tool import CallWorldArchitect
from .plot_architect_tool import CallPlotArchitect
from .casting_director_tool import CallCastingDirector
from .scene_showrunner_tool import CallSceneShowrunner

__all__ = [
    "CallWorldArchitect",
    "CallPlotArchitect",
    "CallCastingDirector",
    "CallSceneShowrunner",
]
