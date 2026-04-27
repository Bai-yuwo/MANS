"""
knowledge_bases/

知识库模块。

所有具体知识库均继承自 base_db.BaseDB，获得异步文件读写、原子写入和并发安全能力。

子模块：
    - base_db: 知识库基类，提供通用的 JSON 持久化操作。
    - bible_db: 世界观知识库，管理世界规则和设定。
    - character_db: 人物知识库，管理人物卡和关系网络。
    - story_db: 故事知识库，管理大纲、弧线、章节规划和草稿。
    - foreshadowing_db: 伏笔知识库，追踪伏笔的全生命周期。
    - style_db: 文风知识库，管理风格配置和范例。
    - geo_db: 地理节点图存储，层级树 + 空间连接图。
    - faction_db: 势力节点网存储，关系网 + 层级结构。
    - cultivation_db: 修为节点链存储，递进链 + 分支结构。
    - tech_db: 科技树节点存储，递进链 + 分支结构。
    - social_db: 社会制度节点存储，层级树结构。
    - setting_db: 通用设定节点存储，扁平分类结构。

存储约定：
    每个知识库在 workspace/{project_id}/ 下拥有独立的子目录，
    数据以 JSON 文件形式存储，由 BaseDB 统一管理。
"""

from .base_db import BaseDB
from .bible_db import BibleDB
from .character_db import CharacterDB
from .cultivation_db import CultivationDB
from .faction_db import FactionDB
from .foreshadowing_db import ForeshadowingDB
from .geo_db import GeoDB
from .setting_db import SettingDB
from .social_db import SocialDB
from .story_db import StoryDB
from .style_db import StyleDB
from .tech_db import TechTreeDB

__all__ = [
    "BaseDB",
    "BibleDB",
    "CharacterDB",
    "CultivationDB",
    "FactionDB",
    "ForeshadowingDB",
    "GeoDB",
    "SettingDB",
    "SocialDB",
    "StoryDB",
    "StyleDB",
    "TechTreeDB",
]
