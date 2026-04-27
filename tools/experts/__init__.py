"""
tools/experts/

12 个专家 ExpertTool 子类。每个专家 = 一次性 LLM 调用 → 返回字符串。

主管通过自身 tool_scope 引用对应专家(`call_geographer` / `call_writer` ...)。
专家不写 KB,只返回产物;主管拿到后再调写工具落盘。

完整列表:
    Geographer / RuleSmith / PortraitDesigner / RelationDesigner /
    ArcDesigner / ChapterDesigner / SceneDirector / Writer ★ /
    Critic / ContinuityChecker / Scribe / ReviewManager

★ Writer 是唯一 streaming=True 的专家,主管会在调用前注入 stream sink。
"""

from .geographer import Geographer
from .rule_smith import RuleSmith
from .portrait_designer import PortraitDesigner
from .relation_designer import RelationDesigner
from .arc_designer import ArcDesigner
from .chapter_designer import ChapterDesigner
from .scene_director import SceneDirector
from .writer import Writer
from .consistency_auditor import ConsistencyAuditor
from .critic import Critic
from .continuity_checker import ContinuityChecker
from .scribe import Scribe
from .review_manager import ReviewManager

__all__ = [
    "ConsistencyAuditor",
    "Geographer",
    "RuleSmith",
    "PortraitDesigner",
    "RelationDesigner",
    "ArcDesigner",
    "ChapterDesigner",
    "SceneDirector",
    "Writer",
    "Critic",
    "ContinuityChecker",
    "Scribe",
    "ReviewManager",
]
