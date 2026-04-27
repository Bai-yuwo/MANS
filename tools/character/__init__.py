"""
tools/character/

CastingDirector 主管的 KB 写权限工具组。

包含:
    save_character        — 保存/更新单个角色卡
    save_relationships    — 保存/更新角色间关系网
    delete_character      — 删除角色卡

其他 agent 通过 kb_query/read_character & read_relationships 读取。
"""

from .delete_character import DeleteCharacter
from .save_character import SaveCharacter
from .save_relationships import SaveRelationships

__all__ = ["DeleteCharacter", "SaveCharacter", "SaveRelationships"]
