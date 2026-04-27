"""
agents/managers/

5 个主管(Manager)的具体实现。每个主管都是 BaseAgent 子类,
通过类属性声明 agent_name / system_prompt_path / tool_scope。

Director / WorldArchitect / CastingDirector / PlotArchitect / SceneShowrunner

P2.0 阶段:WorldArchitect 一条竖线进行架构验证。
P2.1 阶段:批量铺其余 4 主管。当前已验证 WorldArchitect / PlotArchitect /
CastingDirector / SceneShowrunner。
P2.2 阶段:Director + Orchestrator 架构合龙。
"""

from .casting_director import CastingDirector
from .director import Director
from .plot_architect import PlotArchitect
from .scene_showrunner import SceneShowrunner
from .world_architect import WorldArchitect

__all__ = [
    "Director",
    "WorldArchitect",
    "PlotArchitect",
    "CastingDirector",
    "SceneShowrunner",
]
