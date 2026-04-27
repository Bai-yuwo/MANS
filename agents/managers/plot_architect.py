"""
agents/managers/plot_architect.py

PlotArchitect 主管 — PLAN 阶段的剧情规划总指挥。

职责边界:
    - 接收用户的故事核心需求(主线、风格、长度、关键人物期待)
    - **自己产出 outline**(没有 OutlineDesigner 专家,这是主管的 reasoning 出口)
    - 调用 ArcDesigner 把 outline 拆分成若干 arc 计划
    - 调用 ChapterDesigner 把单个 arc 拆分成单章场景序列
    - 全程不写 bible / characters / scene_beatsheets

ReAct 行为契约:
    1. 整合用户输入 → save_outline(主管 reasoning 直接写)
    2. 对每个需要细化的 arc → call_arc_designer → save_arc
    3. 对每个需要细化的章节 → call_chapter_designer → save_chapter_plan
    4. 全部落盘后无 tool_call 即结束

P2.1a 验证范围(用户已确认):
    - 1 outline + 1 arc + 1 chapter,验证三层落盘 handshake
    - max_turns=10:比 WorldArchitect 多 2 轮(三层产出比两层多)
"""

from core import BaseAgent


class PlotArchitect(BaseAgent):
    """PLAN 阶段剧情规划主管。统管 outline / arcs / chapter_plans 三层。"""

    agent_name = "PlotArchitect"
    description = (
        "PLAN 阶段剧情规划主管,自产 outline,调用 ArcDesigner / ChapterDesigner "
        "细化 arc 与章节场景序列,落盘到 outline.json / arcs/* / chapters/*_plan.json。"
    )
    system_prompt_path = "system/managers/PlotArchitect.j2"

    # 严格遵循 CLAUDE.md 中 PlotArchitect 的权限定义:
    #   KB 共享读 + 自身写组(story) + 可调专家(ArcDesigner / ChapterDesigner)
    tool_scope = [
        # KB 共享读(读 bible/characters 是合理需求,即使本次验证可能用不到)
        "read_project_meta",
        "read_bible",
        "read_foreshadowing",
        "read_character",
        "read_relationships",
        "read_outline",
        "read_arc",
        "read_chapter_plan",
        "list_arcs",
        "list_chapters",
        "vector_search",
        # 自身写组
        "save_outline",
        "save_arc",
        "save_chapter_plan",
        # 可调专家
        "call_arc_designer",
        "call_chapter_designer",
    ]

    # PLAN 阶段：outline + arcs + chapter_plans，三层产出预计 6-12 轮。
    # 留足余量到 20，防止 LLM 多次检查或重试。
    max_turns = 20
