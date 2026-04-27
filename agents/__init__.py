"""
agents/

主管(Manager)Agent 子包。
所有 BaseAgent 子类按职责分布在 agents/managers/。
专家(Expert)是 ExpertTool 子类,放在 tools/experts/。
"""

from . import managers  # noqa: F401
