"""
tools/experts/review_manager.py

ReviewManager 专家 — WRITE 阶段由 SceneShowrunner 主管在收齐 Critic + ContinuityChecker
issues 后调用,**仲裁审查冲突并打包成 RewriteGuidance**。

为什么需要 ReviewManager(仲裁层):
    Critic 与 ContinuityChecker 并行跑出的 issues 可能矛盾(Critic 说"加描写"
    Continuity 说"删冗余设定"),也可能重复。直接发给 Writer 会让 Writer 在
    多重声音里失声。ReviewManager 负责:
        1. 去重(同一段两个专家提了类似建议)
        2. 化解冲突(优先级:CRITICAL > HIGH > MEDIUM > LOW;同级时倾向 Continuity)
        3. 打包 RewriteGuidance:must_keep / must_change / priority_issues / style_hints
        4. 决定 needs_rewrite(MEDIUM 以上且本场重写次数 < 2)

输出 RewriteGuidance,SceneShowrunner 用 save_rewrite_guidance 落盘。
之后(若 needs_rewrite)主管再调 Writer,把 guidance 拼进 user prompt 触发重写。
"""

from core.expert_tool import ExpertTool
from core.schemas import RewriteGuidance


class ReviewManager(ExpertTool):
    expert_name = "ReviewManager"
    description = (
        "审查仲裁专家:合并 Critic + ContinuityChecker + ConsistencyAuditor 的 issues,化解冲突,"
        "产出 RewriteGuidance(含 needs_rewrite / priority_issues / must_keep / must_change)。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chapter_number": {"type": "integer"},
            "scene_index": {"type": "integer"},
            "rewrite_attempt": {
                "type": "integer",
                "description": "本场已重写次数(0 = 首次审查),用于阻断 ≥2 的死循环。",
            },
            "scene_text": {
                "type": "string",
                "description": "当前(待审)正文,审查时一并阅读。",
            },
            "critic_issues": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Critic 专家产出的 Issue 数组。",
            },
            "continuity_issues": {
                "type": "array",
                "items": {"type": "object"},
                "description": "ContinuityChecker 专家产出的 Issue 数组。",
            },
            "consistency_issues": {
                "type": "array",
                "items": {"type": "object"},
                "description": "ConsistencyAuditor 专家产出的 Issue 数组(按 genre 审计战力/资源/科技/制度等内在一致性)。",
            },
            "beatsheet": {
                "type": "object",
                "description": "本场 SceneBeatsheet,用于判断 must_keep。",
            },
        },
        "required": [
            "chapter_number",
            "scene_index",
            "rewrite_attempt",
            "scene_text",
            "critic_issues",
            "continuity_issues",
            "consistency_issues",
        ],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/ReviewManager.j2"
    user_prompt_template = "user/experts/ReviewManager/arbitrate.j2"

    output_schema = {
        "name": "RewriteGuidance",
        "strict": False,
        "schema": RewriteGuidance.model_json_schema(),
    }
