"""
tools/experts/writer.py

Writer 专家 ★ — WRITE 阶段由 SceneShowrunner 主管调用,**唯一的流式专家**。

为什么 Writer 是流式:
    用户在前端等读正文是最强的"在线感"信号,token-by-token 推送可以让用户
    看到 Writer 的"运笔过程",同时 reasoning 摘要走另一频道供主管诊断。
    其他 11 个专家都是规划/审查性质,一次性返回 JSON 即可。

输入约束:
    - **必读** beatsheet:Writer 不再回看 KB,所有设定都在节拍表里
    - prev_tail:上一场尾段(承接语气)
    - rewrite_guidance(可选):重写时主管把 ReviewManager 仲裁结果拼进来
    - character_voices(可选):语言习惯片段,从 character_voice_keywords 摘要

输出:纯文本场景正文。SceneShowrunner 用 save_scene_draft / save_scene_final 落盘。

为什么 output_schema = None:
    Writer 是 creator 档,温度 0.7,不强约束 JSON。LLMClient.call() 看到 role=creator
    会自动忽略 json_schema 参数。即使误传也不会生效,这里显式置 None 表态。
"""

from core.expert_tool import ExpertTool


class Writer(ExpertTool):
    expert_name = "Writer"
    description = (
        "正文创作专家(唯一流式)。读节拍表 + 上文尾段(+ 重写指南)生成场景正文。"
        "返回纯文本字符串,主管负责落盘。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "beatsheet": {
                "type": "object",
                "description": "SceneBeatsheet 结构(必填)。Writer 不再回看 KB,只看节拍表。",
            },
            "prev_tail": {
                "type": "string",
                "description": "上一场或上一章末尾的最后 1-2 段,用于语气承接。",
            },
            "transition_from_prev": {
                "type": "string",
                "description": "SceneDirector 给出的与上一场衔接提示(时间/空间/情绪连续)。若存在，Writer 需在开头 50 字内自然体现承接关系。",
            },
            "current_draft": {
                "type": "string",
                "description": "重写时主管把上次草稿原文作为 current_draft 传入,Writer 基于现有结构改而非从零写。仅在 rewrite_guidance 存在时使用。",
            },
            "rewrite_guidance": {
                "type": "object",
                "description": "重写时由 SceneShowrunner 注入的 RewriteGuidance(含 must_keep / must_change / priority_issues / style_hints)。",
            },
            "character_voices": {
                "type": "object",
                "description": "出场角色的 voice_keywords 摘要(便于对话语气一致)。",
            },
            "tone_hint": {"type": "string"},
            "target_word_count": {"type": "integer"},
            "genre": {
                "type": "string",
                "description": "作品题材,如 '玄幻' / '科幻' / '都市' / '历史' / '仙侠'。Writer 据此调整写作范式和笔法。",
            },
        },
        "required": ["beatsheet"],
        "additionalProperties": False,
    }
    system_prompt_path = "system/experts/Writer.j2"
    user_prompt_template = "user/experts/Writer/write_scene.j2"

    # creator 档,纯文本输出
    output_schema = None
    streaming = True
