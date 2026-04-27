"""
agents/managers/scene_showrunner.py

SceneShowrunner 主管 — WRITE 阶段单场景流水线总控。

职责边界:
    - 接收用户指定的 chapter_number + scene_index(以及 P2 验证里的「单场景模式」)
    - 拉取 chapter_plan / character / bible / foreshadowing 摘要
    - 编排 6 个 WRITE 专家:
        SceneDirector → Writer → Critic + ContinuityChecker (并行) → ReviewManager
        → (按需重写) → Scribe → apply_kb_diff
    - 把每一步产物落到 KB:
        save_scene_beatsheet / save_scene_draft / save_review_issues
        / save_rewrite_guidance / save_scene_final / apply_kb_diff
    - 不写 bible / characters(那是 WorldArchitect / CastingDirector)
    - 不写 outline / arc / chapter_plan(那是 PlotArchitect)

ReAct 行为契约:
    1. read_chapter_plan(chapter_number)→ 拿到 scenes[i]
    2. read_character(name) × N、read_bible 等 → 整理 character_briefs / world_context / active_foreshadowing
    3. call_scene_director(scene_plan + 摘要) → SceneBeatsheet 草案
       → save_scene_beatsheet 落盘
    4. call_writer(beatsheet, prev_tail) → 草稿正文(流式专家,token 实时推送前端)
       → save_scene_draft 落盘
    5. **并行**调用 call_critic + call_continuity_checker(同一轮 tool_calls 中两条)
       → 收集 ReviewIssues → save_review_issues 落盘
    6. call_review_manager(review_issues, ...) → RewriteGuidance
       → save_rewrite_guidance 落盘
    7. 若 RewriteGuidance.needs_rewrite=true 且 rewrite_attempt < 2:
         call_writer(beatsheet, current_draft=draft, rewrite_guidance) → 重写正文
         → save_scene_draft 覆盖落盘
         → 回到 5
       否则:
         视情况 save_scene_final(只在整章场景全部完成后,验证脚本里走 single-scene 模式时可省略)
    8. call_scribe(scene_text + 当前角色状态) → KB diff
       → apply_kb_diff 落盘
    9. 全部落盘后无 tool_call 即结束

P2.1c 验证范围(用户已确认):
    - 1 scene + 1 轮 rewrite 上限
    - 复用现有 P2 产出(p2-plotarch outline/arc/chapter_plan + p2-casting characters)
    - max_turns=20:5 个写工具 + 6 个专家调用 + 重写循环 ≈ 14-18 轮,留余量

线程模型:
    BaseAgent 已经在 _dispatch_tools 中给 streaming=True 的专家注入 sink_queue,
    Writer 的 token packets 会自动转发到 SceneShowrunner.run() 的 yield 流,
    不需要在本类做额外处理。
"""

from core import BaseAgent


class SceneShowrunner(BaseAgent):
    """WRITE 阶段单场景流水线主管。统管 6 个 WRITE 专家与 5 个写工具。"""

    agent_name = "SceneShowrunner"
    description = (
        "WRITE 阶段单场景流水线主管,编排 SceneDirector→Writer→Critic+Continuity→"
        "ReviewManager→(可选)Writer 重写→Scribe→apply_kb_diff,"
        "落盘到 chapters/scene_beatsheets/* / chapter_*_draft.json / review/* / characters/* (diff)。"
    )
    system_prompt_path = "system/managers/SceneShowrunner.j2"

    # 严格遵循 CLAUDE.md 中 SceneShowrunner 的权限定义:
    #   KB 共享读 + 自身写组(dramaturg / writer / review / system)+ 6 个 WRITE 专家
    tool_scope = [
        # KB 共享读
        "read_project_meta",
        "read_bible",
        "read_foreshadowing",
        "list_characters",
        "read_character",
        "read_relationships",
        "read_outline",
        "read_arc",
        "read_chapter_plan",
        "read_scene_beatsheet",
        "list_scenes",
        "vector_search",
        # 节点详情查询（SceneDirector 转译节拍表时使用）
        "read_geo_node",
        "read_faction_node",
        "read_cultivation_node",
        # 自身写组
        "save_scene_beatsheet",
        "save_scene_draft",
        "save_scene_final",
        "save_review_issues",
        "save_rewrite_guidance",
        "apply_kb_diff",
        # 断点续接
        "read_checkpoint",
        "clear_checkpoint",
        # 6 个 WRITE 专家
        "call_scene_director",
        "call_writer",
        "call_critic",
        "call_continuity_checker",
        "call_review_manager",
        "call_scribe",
    ]

    # P2.1c 验证用,1 场景 + 1 轮 rewrite 预计 14-18 轮,留余量到 20。
    max_turns = 20
