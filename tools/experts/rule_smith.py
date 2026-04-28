"""
tools/experts/rule_smith.py

RuleSmith 专家 — INIT 阶段由 WorldArchitect 主管调用,设计世界规则与战斗体系。

工作模式:
    一次 LLM 调用 → 返回 world_rules + 题材对应节点(cultivation/tech/social/setting)。
    WorldArchitect 用 save_bible 把 world_rules 落入 bible KB，用对应 save_*_node 保存节点。

P2.0 验证阶段:output_schema 用宽松内联 JSON Schema(strict=False),
保证 LLM 返回结构化输出但不强求每个字段精确定义。
"""

from core.expert_tool import ExpertTool


class RuleSmith(ExpertTool):
    expert_name = "RuleSmith"
    description = (
        "设计世界规则(力量体系、修炼境界、战斗机制、能量守恒等)。"
        "根据 genre 产出 cultivation_nodes(修真)、tech_nodes(科幻)、social_nodes(历史/都市)或组合。"
        "返回结构化的 WorldRule 数组与对应节点体系,由 WorldArchitect 审阅后落盘。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "world_brief": {
                "type": "string",
                "description": "世界观一句话概要。",
            },
            "genre": {
                "type": "string",
                "description": (
                    "项目题材，如「科幻」「历史」「玄幻」「仙侠」「武侠」「都市」「西幻」。"
                    "这是决定产出节点类型的最关键输入。"
                ),
            },
            "power_genre": {
                "type": "string",
                "description": "力量/体系类型细分，如「修真」「魔法」「异能」「科技」「内力」「社会制度」。",
            },
            "cultivation_hint": {
                "type": "string",
                "description": "修炼体系额外提示(修真/玄幻题材使用)。",
            },
            "themes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "主题关键词。",
            },
        },
        "required": ["world_brief", "genre"],
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
                    "description": "任何题材都必须产出 8-15 条世界规则。",
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
                "cultivation_nodes": {
                    "type": "array",
                    "description": "修真/玄幻/仙侠/武侠/西幻题材使用。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "tier": {"type": "integer"},
                            "node_type": {"type": "string"},
                            "parent_id": {"type": "string"},
                            "next_ids": {"type": "array", "items": {"type": "string"}},
                            "branch_from": {"type": "string"},
                            "abilities": {"type": "array", "items": {"type": "string"}},
                            "limitations": {"type": "array", "items": {"type": "string"}},
                            "prerequisites": {"type": "array", "items": {"type": "string"}},
                            "description": {"type": "string"},
                        },
                        "required": ["name", "tier"],
                    },
                },
                "cultivation_chain": {
                    "type": "object",
                    "description": "修真/玄幻题材使用。",
                    "properties": {
                        "name": {"type": "string"},
                        "root_id": {"type": "string"},
                        "branch_ids": {"type": "array", "items": {"type": "string"}},
                        "description": {"type": "string"},
                    },
                },
                "tech_nodes": {
                    "type": "array",
                    "description": "科幻/未来/赛博/机甲题材使用。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "tier": {"type": "integer"},
                            "node_type": {"type": "string"},
                            "parent_id": {"type": "string"},
                            "next_ids": {"type": "array", "items": {"type": "string"}},
                            "branch_from": {"type": "string"},
                            "effects": {"type": "array", "items": {"type": "string"}},
                            "limitations": {"type": "array", "items": {"type": "string"}},
                            "prerequisites": {"type": "array", "items": {"type": "string"}},
                            "description": {"type": "string"},
                        },
                        "required": ["name", "tier"],
                    },
                },
                "tech_tree": {
                    "type": "object",
                    "description": "科幻/未来题材使用。",
                    "properties": {
                        "name": {"type": "string"},
                        "root_id": {"type": "string"},
                        "branch_ids": {"type": "array", "items": {"type": "string"}},
                        "description": {"type": "string"},
                    },
                },
                "social_nodes": {
                    "type": "array",
                    "description": "历史/宫斗/权谋/都市/职场题材使用。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "node_type": {"type": "string"},
                            "parent_id": {"type": "string"},
                            "sub_ids": {"type": "array", "items": {"type": "string"}},
                            "privileges": {"type": "array", "items": {"type": "string"}},
                            "obligations": {"type": "array", "items": {"type": "string"}},
                            "influence_scope": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
                "social_system": {
                    "type": "object",
                    "description": "历史/权谋/都市题材使用。",
                    "properties": {
                        "name": {"type": "string"},
                        "root_id": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
                "setting_nodes": {
                    "type": "array",
                    "description": "任何题材均可使用，存放不便归入 cultivation/tech/social 的零散设定。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "category": {"type": "string"},
                            "description": {"type": "string"},
                            "importance": {"type": "string"},
                            "related_node_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "category"],
                    },
                },
            },
            "required": ["world_rules"],
        },
    }
