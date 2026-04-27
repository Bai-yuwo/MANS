"""
agents/managers/world_architect.py

WorldArchitect 主管 — INIT 阶段的世界观总设计师。

职责边界:
    - 对外接收用户的世界观一句话需求(如"清末民初+东方修真+江湖")
    - 调用 Geographer / RuleSmith 两个专家产出地理与规则草案
    - 审阅草案后用 save_bible / append_foreshadowing 落入 KB
    - 不写 outline / characters(那是 PlotArchitect / CastingDirector 的事)

ReAct 行为契约:
    - LLM 第一轮先决定调谁:常见顺序是先 RuleSmith(规则是骨架),再 Geographer(地理依附规则)
    - 拿到专家结果后,在 reasoning 阶段决定字段是否合并/取舍/补充
    - 用 save_bible 落规则、用 append_foreshadowing 落世界级伏笔(可选)
    - 全部落盘后无 tool_call 即结束本次任务

P2.0 验证范围:
    - max_turns=8 给 ReAct 足够腾挪空间但不至于失控烧 token
    - tool_scope 严格按目标架构最小集设置,不放进其它主管的工具
"""

from core import BaseAgent


class WorldArchitect(BaseAgent):
    """INIT 阶段世界观主管。统管世界观、地理、规则。"""

    agent_name = "WorldArchitect"
    description = (
        "INIT 阶段世界观主管,统管世界观/地理/规则的总设计。"
        "调用 Geographer / RuleSmith 专家产出草案,审阅后写入 bible 与 foreshadowing。"
    )
    system_prompt_path = "system/managers/WorldArchitect.j2"

    # 严格遵循 CLAUDE.md 中 WorldArchitect 的权限定义:
    #   KB 共享读 + 自身写组(world) + 可调专家(Geographer / RuleSmith)
    tool_scope = [
        # KB 共享读
        "read_project_meta",
        "read_bible",
        "read_foreshadowing",
        "vector_search",
        # 图结构查询（断点续接时检查已有数据）
        "read_geo_graph",
        "read_geo_node",
        "read_faction_network",
        "read_faction_node",
        "read_cultivation_chain",
        "read_cultivation_node",
        # 新增图/树查询（题材感知节点）
        "read_tech_tree",
        "read_social_system",
        "read_setting",
        # 自身写组
        "save_bible",
        "append_foreshadowing",
        "save_geo_node",
        "save_faction_node",
        "save_cultivation_node",
        # 新增写入工具（题材感知节点）
        "save_tech_node",
        "save_social_node",
        "save_setting_node",
        # 可调专家
        "call_geographer",
        "call_rule_smith",
    ]

    # INIT 阶段包含读 KB + 调专家 + 写 KB，留足余量防止 LLM 多次检查现有数据。
    max_turns = 15
