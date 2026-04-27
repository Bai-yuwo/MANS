"""
tools/experts/arc_designer.py

ArcDesigner 专家 — PLAN 阶段由 PlotArchitect 主管调用,设计单个故事弧。

工作模式:
    一次 LLM 调用 → 返回 arc 级计划(主线节奏、关键事件、章节范围、情绪曲线)。
    PlotArchitect 用 save_arc 落盘到 workspace/{pid}/arcs/arc_{n}.json。

输入:全局 outline + 本 arc 的 brief(目标读者期待、卡点、情绪山峰)。

P2.1a 验证阶段:output_schema 用宽松内联 JSON Schema(strict=False)。
P2-final 阶段如果 schemas.py 增加 StoryArc Pydantic 模型,可改为基于模型派生。
"""

from core.expert_tool import ExpertTool


class ArcDesigner(ExpertTool):
    expert_name = "ArcDesigner"
    description = (
        "设计单个故事弧(arc)的主线节奏、关键事件、章节范围、情绪曲线。"
        "由 PlotArchitect 审阅后落 arcs/arc_{n}.json。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "outline": {
                "type": "object",
                "description": "全局大纲(由 read_outline 读出后传入)。",
            },
            "arc_id": {
                "type": "string",
                "description": "arc 唯一标识,如 'arc_1' / 'arc_2'。",
            },
            "arc_brief": {
                "type": "string",
                "description": "本 arc 的目标读者期待与核心冲突。",
            },
            "chapter_range_hint": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "章节范围 [start, end] 提示,可空,LLM 自行决定。",
            },
        },
        "required": ["outline", "arc_id"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/ArcDesigner.j2"
    user_prompt_template = "user/experts/ArcDesigner/design_arc.j2"

    output_schema = {
        "name": "ArcDesignerOutput",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "arc_id": {"type": "string"},
                "arc_number": {"type": "integer"},
                "arc_theme": {"type": "string"},
                "chapter_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "arc_goal": {"type": "string"},
                "key_events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "chapter_hint": {"type": "integer"},
                            "event": {"type": "string"},
                            "stage": {
                                "type": "string",
                                "enum": ["铺垫", "转折", "高潮", "收束"],
                            },
                        },
                        "required": ["event"],
                    },
                },
                "emotional_curve": {"type": "string"},
                "main_conflict": {"type": "string"},
                "supporting_subplots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subplot": {"type": "string"},
                            "tied_to_chapter": {"type": "integer"},
                            "purpose": {"type": "string"},
                        },
                    },
                },
                "stage_plan": {
                    "type": "object",
                    "properties": {
                        "setup": {"type": "string"},
                        "rising": {"type": "string"},
                        "climax": {"type": "string"},
                        "resolution": {"type": "string"},
                    },
                },
                "is_placeholder": {"type": "boolean"},
            },
            "required": ["arc_id", "arc_theme", "chapter_range", "arc_goal", "key_events"],
        },
    }
