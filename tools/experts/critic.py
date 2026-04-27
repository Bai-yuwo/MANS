"""
tools/experts/critic.py

Critic 专家 — WRITE 阶段由 SceneShowrunner 主管**与 ContinuityChecker 并行**调用。

工作模式:
    一次 LLM 调用 → 输出 list[Issue],focus 在「文学性」:
        - 节奏(冗余/拖沓/突兀切换)
        - 人物刻画(动机不立、对话失声)
        - 描写浓度(感官信息密度)
        - 文风一致性
    设定连贯性问题由 ContinuityChecker 负责,Critic 不重复指出。

输出后:
    SceneShowrunner 把 Critic + ContinuityChecker 的 issues 合并打包,丢给
    ReviewManager 做仲裁;不会直接发给 Writer 重写。
"""

from core.expert_tool import ExpertTool
from core.schemas import Issue, SceneQualityScores


class Critic(ExpertTool):
    expert_name = "Critic"
    description = (
        "文学性审查专家:节奏/人物/描写/文风/情绪价值。返回 issues + scores,"
        "由 SceneShowrunner 合并后交 ReviewManager 仲裁。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chapter_number": {"type": "integer"},
            "scene_index": {"type": "integer"},
            "scene_text": {
                "type": "string",
                "description": "Writer 产出的场景正文(必填)。",
            },
            "beatsheet": {
                "type": "object",
                "description": "本场 SceneBeatsheet(用于审视节拍兑现度)。",
            },
            "character_voices": {
                "type": "object",
                "description": "出场角色 voice_keywords,审视对话是否符合语气。",
            },
            "tone_hint": {"type": "string"},
            "prev_tail": {
                "type": "string",
                "description": "上一场尾段(用于核对承接)。",
            },
            "opening_hook": {
                "type": "string",
                "description": "beatsheet.opening_hook,场景开头设计意图(用于评价 hook 兑现度)。",
            },
            "closing_hook": {
                "type": "string",
                "description": "beatsheet.closing_hook,场景结尾悬念设计(用于评价 expectation 兑现度)。",
            },
            "scene_metrics": {
                "type": "object",
                "description": "SceneMetricsCalculator 产出的量化指标(word_count/protagonist_action_ratio/scene_transition_count/dialogue_to_action_ratio 等)。",
            },
            "rewrite_attempt": {
                "type": "integer",
                "description": "当前是第几次审查(0=首稿,1=第一次重写后,...)。重写后审查应只关注 priority_issues 是否解决。",
            },
        },
        "required": ["scene_text", "beatsheet"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/Critic.j2"
    user_prompt_template = "user/experts/Critic/review_scene.j2"

    output_schema = {
        "name": "CriticIssues",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "issues": {
                    "type": "array",
                    "items": Issue.model_json_schema(),
                    "description": "发现的问题集合(含 severity/type/description/suggestion)。",
                },
                "scores": {
                    "type": "object",
                    "properties": {
                        "emotion_arc_score": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                            "description": "情绪曲线评分:起→承→转→合四段是否完整(1=断裂,5=饱满)",
                        },
                        "anticipation_score": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                            "description": "期待感评分:每300字内是否有新的信息差/悬念/未知因素驱动翻页(1=平铺直叙,5=层层递进)",
                        },
                        "payoff_satisfaction": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 5,
                            "description": "爽点释放评分:铺垫与释放比例是否失衡(0=非爽点场景,1=严重失衡,5=酣畅淋漓)",
                        },
                    },
                    "required": ["emotion_arc_score", "anticipation_score", "payoff_satisfaction"],
                    "description": "场景质量评分。任一项≤2时必须在issues中附带severity='high'的对应issue。",
                },
            },
            "required": ["issues", "scores"],
        },
    }
