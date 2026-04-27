"""
tools/experts/relation_designer.py

RelationDesigner 专家 — INIT 阶段由 CastingDirector 主管调用,设计角色关系网。

工作模式:
    一次 LLM 调用 → 返回 list[Relationship] + 关键关系图说明。
    CastingDirector 用 save_relationships 落盘。

注意:
    输入是已塑型的 character_list(每个含 name + role_brief),
    输出双向引用(A→B 与 B→A 单独存),便于 KB 查询时按角色拉关系。
"""

from core.expert_tool import ExpertTool
from core.schemas import Relationship


class RelationDesigner(ExpertTool):
    expert_name = "RelationDesigner"
    description = (
        "根据已塑型的角色列表设计关系网,产出双向 Relationship 数组(亲情/师徒/敌对/暧昧等)。"
        "由 CastingDirector 审阅后写入 characters/relationships.json。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "characters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role_brief": {"type": "string"},
                    },
                    "required": ["name"],
                },
                "description": "已塑型的角色简表(name + role_brief 即可)。",
            },
            "world_context": {"type": "string"},
            "story_goal_hint": {
                "type": "string",
                "description": "故事核心冲突线索,影响关系强弱与立场。",
            },
        },
        "required": ["characters"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/RelationDesigner.j2"
    user_prompt_template = "user/experts/RelationDesigner/design_relationships.j2"

    output_schema = {
        "name": "RelationshipSet",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "relationships": {
                    "type": "array",
                    "items": Relationship.model_json_schema(),
                }
            },
            "required": ["relationships"],
        },
    }
