"""
tools/experts/chapter_designer.py

ChapterDesigner 专家 — PLAN 阶段由 PlotArchitect 主管调用,产出单章场景序列。

工作模式:
    一次 LLM 调用 → 返回 ChapterPlan(含 scenes:[ScenePlan...])。
    PlotArchitect 用 save_chapter_plan 落盘。

注意:
    本专家**只规划场景骨架**(每场目标、参与者、地点、节拍提示),
    不产出节拍表细节(那是 SceneDirector 的工作)。
"""

from core.expert_tool import ExpertTool
from core.schemas import ChapterPlan


class ChapterDesigner(ExpertTool):
    expert_name = "ChapterDesigner"
    description = (
        "为单章规划场景序列(每场含目标/角色/地点/冲突),返回符合 ChapterPlan 的 JSON。"
        "由 PlotArchitect 审阅后写入 chapters/chapter_{n}_plan.json。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chapter_number": {"type": "integer"},
            "arc_plan": {
                "type": "object",
                "description": "本章所属 arc 的计划(由 read_arc 读出)。",
            },
            "previous_chapter_summary": {
                "type": "string",
                "description": "上一章末尾摘要,影响开场承接。",
            },
            "chapter_brief": {
                "type": "string",
                "description": "本章核心目标(主线推进点 / 情绪山峰 / 设定揭示)。",
            },
            "target_word_count": {"type": "integer"},
        },
        "required": ["chapter_number", "arc_plan", "chapter_brief"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/ChapterDesigner.j2"
    user_prompt_template = "user/experts/ChapterDesigner/design_chapter.j2"

    output_schema = {
        "name": "ChapterPlan",
        "strict": False,
        "schema": ChapterPlan.model_json_schema(),
    }
