"""
tools/experts/geographer.py

Geographer 专家 — INIT 阶段由 WorldArchitect 主管调用,设计地理地图、势力、组织。

工作模式:
    一次 LLM 调用 → 返回 locations/organizations 草案。
    WorldArchitect 接收后审阅,可选地把"地理类规则"打包进 save_bible。

P2.0 验证阶段:output_schema 用宽松内联 JSON Schema(strict=False),
保证 LLM 给出结构化输出但不强求每个字段精确定义。
"""

from core.expert_tool import ExpertTool


class Geographer(ExpertTool):
    expert_name = "Geographer"
    description = (
        "设计世界地理(大陆/区域/关键地点)与势力组织(门派/王国/秘境)。"
        "返回 location_set 与 organization_set 草案,由 WorldArchitect 审阅后合并 bible。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "world_brief": {
                "type": "string",
                "description": "世界观一句话概要(由 WorldArchitect 主管整合用户输入后提供)。",
            },
            "genre": {
                "type": "string",
                "description": "项目题材，如「科幻」「历史」「玄幻」「仙侠」「武侠」「都市」「西幻」。Geographer 据此调整地理与势力描述的术语风格。",
            },
            "themes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "主题/题材关键词,如 ['玄幻','门派','复仇']。",
            },
            "scale_hint": {
                "type": "string",
                "description": "世界规模提示,如 '一州十国' / '一座孤城'。",
            },
        },
        "required": ["world_brief", "genre"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/Geographer.j2"
    user_prompt_template = "user/experts/Geographer/design_world_map.j2"

    output_schema = {
        "name": "GeographerOutput",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "locations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "scale": {"type": "string"},
                            "description": {"type": "string"},
                            "factional_tension": {"type": "string"},
                        },
                        "required": ["name", "description"],
                    },
                },
                "organizations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "stance": {"type": "string"},
                            "description": {"type": "string"},
                            "rivalry": {"type": "string"},
                        },
                        "required": ["name", "description"],
                    },
                },
            },
            "required": ["locations", "organizations"],
        },
    }
