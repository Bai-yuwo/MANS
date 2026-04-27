"""
tools/experts/rule_smith.py

RuleSmith 专家 — INIT 阶段由 WorldArchitect 主管调用,设计世界规则与战斗体系。

工作模式:
    一次 LLM 调用 → 返回 world_rules + cultivation_levels + combat_system 草案。
    WorldArchitect 用 save_bible 把 world_rules 落入 bible KB(category+content+importance 三字段直通 WorldRule schema)。

P2.0 验证阶段:output_schema 用宽松内联 JSON Schema(strict=False),
保证 LLM 返回 world_rules 数组,但不强求 cultivation_levels / combat_system 完美。
"""

from core.expert_tool import ExpertTool


class RuleSmith(ExpertTool):
    expert_name = "RuleSmith"
    description = (
        "设计世界规则(力量体系、修炼境界、战斗机制、能量守恒等)。"
        "返回结构化的 WorldRule 数组与 CombatSystem 配置,由 WorldArchitect 审阅后落 bible。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "world_brief": {"type": "string"},
            "power_genre": {
                "type": "string",
                "description": "力量类型,如 '修真' / '魔法' / '异能'。",
            },
            "cultivation_hint": {"type": "string"},
            "themes": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["world_brief"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/RuleSmith.j2"
    user_prompt_template = "user/experts/RuleSmith/design_rules.j2"

    output_schema = {
        "name": "RuleSmithOutput",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "world_rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": [
                                    "cultivation",
                                    "geography",
                                    "social",
                                    "physics",
                                    "special",
                                ],
                            },
                            "content": {"type": "string"},
                            "importance": {
                                "type": "string",
                                "enum": ["critical", "major", "minor"],
                            },
                        },
                        "required": ["category", "content"],
                    },
                },
                "cultivation_levels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "tier": {"type": "integer"},
                            "description": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
                "combat_system": {
                    "type": "object",
                    "properties": {
                        "core_principle": {"type": "string"},
                        "key_mechanics": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["world_rules"],
        },
    }
