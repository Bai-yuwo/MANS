"""
agents/managers/casting_director.py

CastingDirector 主管 — INIT 阶段角色塑型与关系网总负责人。

职责边界:
    - 接收用户对角色阵容的需求(主角设定、对手定位、辅助角色作用)
    - **按角色名循环调用 PortraitDesigner**(每次只塑型一个角色,避免上下文过载)
    - 全员塑型完成后,调用 RelationDesigner 设计角色关系网
    - 用 save_character 把每张角色卡落盘,用 save_relationships 落关系网
    - 不写 bible / outline(那是 WorldArchitect / PlotArchitect)

ReAct 行为契约:
    1. 解析用户输入的角色清单(主角 + 对手 + 导师等)
    2. 对每个角色 call_portrait_designer → 拿到 CharacterCard 草案 → save_character
       (这是验证「主管循环调同一专家」模式的核心环节)
    3. 全员落盘后调用 call_relation_designer(传 [{name, role_brief}, ...])
       → 拿到 relationships 数组 → save_relationships
    4. 全部落盘后无 tool_call 即结束

P2.1b 验证范围(用户已确认):
    - 3 角色:主角 + 对手 + 导师,验证 PortraitDesigner 被循环调用 3 次
    - max_turns=12:3×(call_expert + save) + RelationDesigner + save_relationships ≈ 8 轮,留余量
"""

from core import BaseAgent


class CastingDirector(BaseAgent):
    """INIT 阶段角色塑型主管。统管角色卡 + 关系网。"""

    agent_name = "CastingDirector"
    description = (
        "INIT 阶段角色塑型主管,按角色名循环调用 PortraitDesigner 产出单角色卡,"
        "全员到齐后调用 RelationDesigner 设计关系网,落盘到 characters/。"
    )
    system_prompt_path = "system/managers/CastingDirector.j2"

    # 严格遵循 CLAUDE.md 中 CastingDirector 的权限定义:
    #   KB 共享读 + 自身写组(character) + 可调专家(PortraitDesigner / RelationDesigner)
    tool_scope = [
        # KB 共享读(读 bible 是合理需求 — 给专家传 world_context)
        "read_project_meta",
        "read_bible",
        "list_characters",
        "read_character",
        "read_relationships",
        "vector_search",
        # 势力查询（角色与势力关联）
        "read_faction_network",
        "read_faction_node",
        # 自身写组
        "save_character",
        "save_relationships",
        "delete_character",
        # 可调专家
        "call_portrait_designer",
        "call_relation_designer",
    ]

    # INIT 角色设计：3 角色 × 2 步(call+save) + 关系网 2 步 + 前置 read 检查 ≈ 10-14 轮。
    # 留足余量到 25，防止 LLM 多次检查现有数据或重试。
    max_turns = 25
