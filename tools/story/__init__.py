"""
tools/story/

PlotArchitect 主管的 KB 写权限工具组。

包含:
    save_outline       — 全局大纲
    save_arc           — 单个故事弧规划
    save_chapter_plan  — 章节场景序列规划

其他 agent 通过 kb_query/read_outline & read_arc & read_chapter_plan 读取。
"""

from .save_outline import SaveOutline
from .save_arc import SaveArc
from .save_chapter_plan import SaveChapterPlan

__all__ = ["SaveOutline", "SaveArc", "SaveChapterPlan"]
