"""
tools/experts/scene_director.py

SceneDirector 专家 — WRITE 阶段由 SceneShowrunner 主管调用,负责剧作转译。

为什么需要 SceneDirector(剧作转译层):
    Writer 严禁直接读 KB 字典,否则会写成"复述设定"的硬科普。SceneDirector 把
    {scene_plan + KB 摘要} 翻译成 SceneBeatsheet:
        - sensory_requirements   感官要求(画面、声音、气味、触觉、味觉)
        - action_beats[]         动作节拍(主体 → 行为 → 影响)
        - emotional_beats[]      情绪节拍(角色 → 情绪 → 触发点)
    Writer 拿到节拍表后只产出剧情正文,不再回看 KB。

输出 SceneBeatsheet,SceneShowrunner 用 save_scene_beatsheet 落盘。
"""

from core.expert_tool import ExpertTool
from core.schemas import SceneBeatsheet


class SceneDirector(ExpertTool):
    expert_name = "SceneDirector"
    description = (
        "把 ScenePlan + KB 摘要翻译成 SceneBeatsheet(感官要求 + 动作节拍 + 情绪节拍)。"
        "Writer 写本场前必读节拍表,严禁绕过。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chapter_number": {"type": "integer"},
            "scene_index": {"type": "integer"},
            "scene_plan": {
                "type": "object",
                "description": "本场场景规划(来自 chapter_{n}_plan.json 的 scenes[i])。",
            },
            "character_briefs": {
                "type": "array",
                "items": {"type": "object"},
                "description": "出场角色当前状态摘要(由 SceneShowrunner 拉 KB 整理后传入)。",
            },
            "world_context": {
                "type": "string",
                "description": "相关 bible 规则与地点描述摘要。",
            },
            "active_foreshadowing": {
                "type": "array",
                "items": {"type": "object"},
                "description": "本章应触发或保持张力的伏笔。",
            },
            "tone_hint": {
                "type": "string",
                "description": "情绪基调,如 '压抑' / '热血'。可被 search_style_examples 引用。",
            },
            "genre": {
                "type": "string",
                "description": "作品题材,如 '玄幻' / '科幻' / '都市' / '历史' / '仙侠'。SceneDirector 据此调整节拍风格。",
            },
        },
        "required": ["chapter_number", "scene_index", "scene_plan", "genre"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/SceneDirector.j2"
    user_prompt_template = "user/experts/SceneDirector/translate_scene.j2"

    output_schema = {
        "name": "SceneBeatsheet",
        "strict": False,
        "schema": SceneBeatsheet.model_json_schema(),
    }
