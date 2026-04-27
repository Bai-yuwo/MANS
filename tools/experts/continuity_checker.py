"""
tools/experts/continuity_checker.py

ContinuityChecker 专家 — WRITE 阶段由 SceneShowrunner 主管**与 Critic 并行**调用。

工作模式:
    一次 LLM 调用 → 输出 list[Issue],focus 在「设定连贯」:
        - 角色状态(境界/伤情/位置)与 KB 是否一致
        - 时间线先后(season/天数/节庆)
        - 伏笔触发与回收
        - bible 规则(力量体系/世界观)是否被违反
    文学性问题由 Critic 负责,本专家只关心可被 KB 验证的"硬错误"。

注意:
    本专家**需要 KB 上下文**(character_state / active_foreshadowing / world_rules),
    所以主管在调用前会先用 read_character / read_foreshadowing / read_bible 拉数据,
    再把摘要塞进 input_schema 的 kb_context 字段。
"""

from core.expert_tool import ExpertTool
from core.schemas import Issue


class ContinuityChecker(ExpertTool):
    expert_name = "ContinuityChecker"
    description = (
        "设定连贯性审查专家:角色状态/时间线/伏笔/世界规则。返回 list[Issue],"
        "由 SceneShowrunner 合并后交 ReviewManager 仲裁。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "scene_text": {"type": "string"},
            "chapter_number": {"type": "integer"},
            "scene_index": {"type": "integer"},
            "character_states": {
                "type": "array",
                "items": {"type": "object"},
                "description": "本场出场角色当前状态(境界/位置/活跃目标)。",
            },
            "active_foreshadowing": {
                "type": "array",
                "items": {"type": "object"},
                "description": "本章应触发或保持张力的伏笔(由 read_foreshadowing 拉取)。",
            },
            "world_rules": {
                "type": "array",
                "items": {"type": "object"},
                "description": "相关 bible 规则摘要。",
            },
            "previous_scene_summary": {
                "type": "string",
                "description": "上一场摘要(用于核对时间线/状态承接)。",
            },
            "rewrite_attempt": {
                "type": "integer",
                "description": "当前是第几次审查(0=首稿,1=第一次重写后,...)。重写后审查应只关注 priority_issues 是否解决。",
            },
        },
        "required": ["scene_text", "chapter_number", "scene_index"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/ContinuityChecker.j2"
    user_prompt_template = "user/experts/ContinuityChecker/check_continuity.j2"

    output_schema = {
        "name": "ContinuityIssues",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "issues": {
                    "type": "array",
                    "items": Issue.model_json_schema(),
                }
            },
            "required": ["issues"],
        },
    }
