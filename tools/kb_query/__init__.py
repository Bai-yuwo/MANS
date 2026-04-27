"""
tools/kb_query/

知识库共享只读工具组 — 所有 agent(主管 + 专家)均可调用。

22 个 tool:
    read_project_meta / read_bible / read_foreshadowing
    read_character / read_relationships / read_outline
    read_arc / read_chapter_plan / read_scene_beatsheet
    list_characters / list_arcs / list_chapters / list_scenes
    vector_search / search_kb_text
    # 新增图查询工具
    read_geo_graph / read_geo_node / traverse_geo
    read_faction_network / read_faction_node
    read_cultivation_chain / read_cultivation_node
    read_tech_tree / read_social_system / read_setting

实现要点:
    - 全部为 BaseTool 直接子类(不调 LLM,纯 IO)。
    - project_id 由 ContextVar 注入,LLM 不需在 input_schema 中重复给。
    - 后端复用 knowledge_bases/ 下的 BaseDB 子类与 vector_store.VectorStore。
"""

from .list_arcs import ListArcs
from .list_chapters import ListChapters
from .list_characters import ListCharacters
from .list_scenes import ListScenes
from .read_arc import ReadArc
from .read_bible import ReadBible
from .read_chapter_plan import ReadChapterPlan
from .read_character import ReadCharacter
from .read_cultivation_chain import ReadCultivationChain
from .read_cultivation_node import ReadCultivationNode
from .read_faction_network import ReadFactionNetwork
from .read_faction_node import ReadFactionNode
from .read_foreshadowing import ReadForeshadowing
from .read_geo_graph import ReadGeoGraph
from .read_geo_node import ReadGeoNode
from .read_outline import ReadOutline
from .read_project_meta import ReadProjectMeta
from .read_relationships import ReadRelationships
from .read_scene_beatsheet import ReadSceneBeatsheet
from .scene_metrics_calculator import SceneMetricsCalculator
from .read_setting import ReadSetting
from .read_social_system import ReadSocialSystem
from .read_tech_tree import ReadTechTree
from .search_kb_text import SearchKBText
from .traverse_geo import TraverseGeo
from .vector_search import VectorSearch

__all__ = [
    "ListArcs",
    "ListChapters",
    "ListCharacters",
    "ListScenes",
    "ReadArc",
    "ReadBible",
    "ReadChapterPlan",
    "ReadCharacter",
    "ReadCultivationChain",
    "ReadCultivationNode",
    "ReadFactionNetwork",
    "ReadFactionNode",
    "ReadForeshadowing",
    "ReadGeoGraph",
    "ReadGeoNode",
    "ReadOutline",
    "ReadProjectMeta",
    "ReadRelationships",
    "ReadSceneBeatsheet",
    "SceneMetricsCalculator",
    "ReadSetting",
    "ReadSocialSystem",
    "ReadTechTree",
    "SearchKBText",
    "TraverseGeo",
    "VectorSearch",
]
