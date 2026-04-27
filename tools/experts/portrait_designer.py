"""
tools/experts/portrait_designer.py

PortraitDesigner 专家 — INIT 阶段由 CastingDirector 主管调用,产出单个角色画像。

工作模式:
    一次 LLM 调用 → 返回 CharacterCard 草案(画像/性格/动机/语言习惯)。
    CastingDirector 审阅后用 save_character 落盘。

注意:
    本专家**只输出单角色卡**;若需要批量产出,主管按角色名循环多次调用,
    避免单次 LLM 上下文过载导致角色辨识度下降。
"""

from core.expert_tool import ExpertTool
from core.schemas import CharacterCard


class PortraitDesigner(ExpertTool):
    expert_name = "PortraitDesigner"
    description = (
        "为单个角色生成画像、性格内核、动机与语言习惯。"
        "返回符合 CharacterCard 的 JSON,由 CastingDirector 审阅后落盘。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "character_name": {
                "type": "string",
                "description": "角色名(必填,可中文)。",
            },
            "role_brief": {
                "type": "string",
                "description": "该角色在故事中的定位,如 '主角的师父'、'反派一号'。",
            },
            "world_context": {
                "type": "string",
                "description": "世界观背景片段,帮助语言风格、修炼体系一致。",
            },
            "is_protagonist": {"type": "boolean"},
        },
        "required": ["character_name", "role_brief"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/PortraitDesigner.j2"
    user_prompt_template = "user/experts/PortraitDesigner/design_portrait.j2"

    output_schema = {
        "name": "CharacterCard",
        "strict": True,
        "schema": CharacterCard.model_json_schema(),
    }
