"""
tools/experts/scribe.py

Scribe 专家 — WRITE 阶段终稿 confirmed 后由 SceneShowrunner 主管调用。

工作模式:
    一次 LLM 调用 → 从终稿 + 当前 KB 状态产出 KB diff:
        {
            "characters":     [{"name": "...", "patch": {...}}],
            "foreshadowing":  {"add": [...], "update": [...]},
            "bible":          {"add": [...]}
        }
    SceneShowrunner 把 diff 喂给 apply_kb_diff,完成"剧情→KB"的回流闭环。

为什么由 Scribe 算 diff,而不是直接写 KB:
    1. KB 写权限只归口主管,专家不写 KB(架构原则之一)。
    2. Scribe 输出 diff 形态便于审计(可回看每一场场景产生了哪些设定变化)。
    3. 主管对 diff 仍可加策略层:阈值、用户确认、批处理。

注意:
    Scribe 输出的 diff 不要修改主线设定(world rules 应当 immutable);仅追加。
"""

from core.expert_tool import ExpertTool


class Scribe(ExpertTool):
    expert_name = "Scribe"
    description = (
        "从终稿提取 KB 增量(角色状态变化/伏笔触发/新规则),返回 diff 字典。"
        "SceneShowrunner 用 apply_kb_diff 落到对应 KB。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "chapter_number": {"type": "integer"},
            "scene_index": {"type": "integer"},
            "scene_text": {
                "type": "string",
                "description": "终稿场景正文(必填)。",
            },
            "current_character_states": {
                "type": "array",
                "items": {"type": "object"},
                "description": "本场之前的角色状态快照,Scribe 据此算 patch。",
            },
            "active_foreshadowing": {
                "type": "array",
                "items": {"type": "object"},
            },
        },
        "required": ["chapter_number", "scene_index", "scene_text"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/Scribe.j2"
    user_prompt_template = "user/experts/Scribe/extract_diff.j2"

    # KBDiff 输出 schema(loose,strict=False:允许专家 patch 字段自由扩展,
    # apply_kb_diff 内部会对 ForeshadowingItem / WorldRule 字段做严格校验)
    output_schema = {
        "name": "KBDiff",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "characters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "角色名,必须与 KB 中 character.name 完全一致",
                            },
                            "patch": {
                                "type": "object",
                                "description": (
                                    "增量字段。仅放本场内发生变化的字段(current_location / "
                                    "current_emotion / active_goals / last_updated_chapter 等);"
                                    "深度合并到 KB,空写会污染。"
                                ),
                                "additionalProperties": True,
                            },
                        },
                        "required": ["name", "patch"],
                        "additionalProperties": False,
                    },
                },
                "foreshadowing": {
                    "type": "object",
                    "properties": {
                        "add": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "description": "新埋伏笔,字段对齐 ForeshadowingItem schema",
                                "additionalProperties": True,
                            },
                        },
                        "update": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["planted", "hinted", "triggered", "resolved"],
                                    },
                                    "notes": {"type": "string"},
                                    "triggered_chapter": {"type": "integer"},
                                },
                                "required": ["id", "status"],
                                "additionalProperties": True,
                            },
                        },
                    },
                    "additionalProperties": False,
                },
                "bible": {
                    "type": "object",
                    "properties": {
                        "add": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "description": "新世界规则,字段对齐 WorldRule schema",
                                "additionalProperties": True,
                            },
                        }
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["characters", "foreshadowing", "bible"],
            "additionalProperties": False,
        },
    }
