"""
agents/managers/director.py

Director 主管 — MANS 全局编排主管。

职责边界:
    - 阶段流转:INIT → PLAN → WRITE → COMPLETED
    - 调度子主管:WorldArchitect / CastingDirector / PlotArchitect / SceneShowrunner
    - 用户确认:阶段切换处调用 confirm_stage_advance,等待用户回复
    - 状态管理:write_project_meta 更新 stage / current_chapter
    - 不写正文、不写世界观、不写角色卡、不写大纲

ReAct 行为契约:
    1. read_project_meta → 读当前 stage
    2. 按 stage 调度对应子主管(通过 ManagerTool)
    3. 子主管完成后 → confirm_stage_advance(总结成果 + 请求确认)
    4. 收到确认后 → write_project_meta(更新 stage) → 调度下一子主管
    5. 全部完成 → write_project_meta(stage="COMPLETED")

与 Orchestrator 的协作:
    - Director.run() 会 yield 所有子主管的 packets(包括 Writer 流式)
    - 当 confirm_stage_advance 被调用时,run() 会 yield type="confirm" 的 StreamPacket
      并自然退出 ReAct 循环
    - Orchestrator 拦截 confirm 包,暂停 Director,等用户回复
    - 用户回复后,Orchestrator 重新调用 Director.run(user_prompt=reply,
      previous_response_id=director.last_response_id) 续接会话
"""

from core import BaseAgent


class Director(BaseAgent):
    """全局编排主管。推进 INIT → PLAN → WRITE 阶段,调度 4 个业务主管,处理用户确认。"""

    agent_name = "Director"
    description = (
        "全局编排主管,负责阶段流转(INIT→PLAN→WRITE→COMPLETED)、调度 4 个业务主管、"
        "阶段切换处请求用户确认。不生成正文,不写 KB,只做编排与状态管理。"
    )
    system_prompt_path = "system/managers/Director.j2"

    # Director 的权限:读 meta + 写 meta + 确认 + 4 个子主管 + 图查询
    tool_scope = [
        # 读
        "read_project_meta",
        "read_bible",
        "read_foreshadowing",
        "list_characters",
        "read_outline",
        "list_arcs",
        "list_chapters",
        # 图结构查询（检查数据完整性）
        "read_geo_graph",
        "read_geo_node",
        "traverse_geo",
        "read_faction_network",
        "read_faction_node",
        "read_cultivation_chain",
        "read_cultivation_node",
        # 写
        "write_project_meta",
        # 确认/询问
        "confirm_stage_advance",
        "ask_user",
        # 子主管
        "call_world_architect",
        "call_casting_director",
        "call_plot_architect",
        "call_scene_showrunner",
    ]

    # Director 需要调度多个子主管 + 阶段确认 + 错误恢复，留足余量。
    max_turns = 25
