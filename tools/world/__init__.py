"""
tools/world/

WorldArchitect 主管的 KB 写权限工具组。

包含:
    save_bible            — 保存/更新 bible(世界观规则)
    append_foreshadowing  — 追加伏笔条目
    save_geo_node         — 保存/更新地理节点
    save_faction_node     — 保存/更新势力节点
    save_cultivation_node — 保存/更新修为节点
    save_tech_node        — 保存/更新科技节点
    save_social_node      — 保存/更新社会制度节点
    save_setting_node     — 保存/更新通用设定节点

仅由 WorldArchitect 持有写权限。其他 agent 若需要查 bible/伏笔/地理/势力/修为,走 kb_query/。
"""

from .append_foreshadowing import AppendForeshadowing
from .save_bible import SaveBible
from .save_cultivation_node import SaveCultivationNode
from .save_faction_node import SaveFactionNode
from .save_geo_node import SaveGeoNode
from .save_setting_node import SaveSettingNode
from .save_social_node import SaveSocialNode
from .save_tech_node import SaveTechNode

__all__ = [
    "AppendForeshadowing",
    "SaveBible",
    "SaveCultivationNode",
    "SaveFactionNode",
    "SaveGeoNode",
    "SaveSettingNode",
    "SaveSocialNode",
    "SaveTechNode",
]
