"""
tools/experts/consistency_auditor.py

ConsistencyAuditor 专家 — WRITE 阶段与 Critic、ContinuityChecker **并行**调用的第三位审查专家。

职责边界:
    - 按题材(genre)差异化审计场景设定的内在一致性
    - 关注「规则层面的硬错误」,而非文学性或时间线衔接
    - 与 ContinuityChecker 的区别:
        - ContinuityChecker: 跨场景状态衔接(角色位置/时间线/伏笔)
        - ConsistencyAuditor: 单场景内设定自洽(战力/资源/科技/制度)

按 genre 切换审计维度:
    - 修仙/玄幻: power_level(境界差距)、resource_mismatch(法宝匹配)
    - 科幻: tech_level(科技代差)、physics_violation(物理规则)
    - 都市: social_status(社会关系)、resource_mismatch(资金/人脉)
    - 历史: institutional(制度合规)、anachronism(时代错位)

输出: list[Issue],结构同 Critic,但 type 为 consistency/power_level/resource_mismatch/timeline_error/tech_level/physics_violation/social_status/institutional/anachronism/other。
每条 issue 必须有 affected_characters 和 rule_reference。
"""

from core.expert_tool import ExpertTool
from core.schemas import Issue


class ConsistencyAuditor(ExpertTool):
    expert_name = "ConsistencyAuditor"
    description = (
        "设定内在一致性审计专家:按题材差异化审查战力/资源/科技/制度等规则的内在自洽。"
        "返回 list[Issue],由 SceneShowrunner 合并后交 ReviewManager 仲裁。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "scene_text": {"type": "string", "description": "Writer 产出的场景正文(必填)。"},
            "chapter_number": {"type": "integer"},
            "scene_index": {"type": "integer"},
            "genre": {
                "type": "string",
                "description": "作品题材(修仙/玄幻/科幻/都市/历史/仙侠等),决定审计维度。",
            },
            "character_states": {
                "type": "array",
                "items": {"type": "object"},
                "description": "出场角色当前状态(境界/职位/资源/法宝等)。",
            },
            "world_rules": {
                "type": "array",
                "items": {"type": "object"},
                "description": "相关 bible 规则摘要(力量体系/物理规则/社会制度等)。",
            },
            "beatsheet": {
                "type": "object",
                "description": "SceneBeatsheet(用于核对 narrative_function 与场景实际内容是否匹配)。",
            },
            "rewrite_attempt": {
                "type": "integer",
                "description": "当前是第几次审查(0=首稿,1=第一次重写后,...)。",
            },
        },
        "required": ["scene_text", "genre"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/ConsistencyAuditor.j2"
    user_prompt_template = "user/experts/ConsistencyAuditor/audit_scene.j2"

    output_schema = {
        "name": "ConsistencyIssues",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "issues": {
                    "type": "array",
                    "items": Issue.model_json_schema(),
                    "description": "发现的一致性问题(含 severity/type/description/affected_characters/rule_reference)。",
                }
            },
            "required": ["issues"],
        },
    }
